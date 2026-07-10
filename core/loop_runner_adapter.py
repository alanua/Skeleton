from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from core.loop_controller import LoopDecision, LoopEvent, LoopState, advance_loop
from core.loop_engine import LoopEngine, LoopStepResult
from core.loop_recovery_packet import (
    LoopRecoveryApprovalError,
    LoopRecoveryPacket,
    LoopRecoveryPacketError,
    TrustedLoopRecoveryApproval,
    loop_recovery_packet_hash,
)
from core.loop_policy_registry import resolve_loop_policy_profile
from core.loop_state_store import (
    LoopStateConflictError,
    LoopStateCorruptionError,
    LoopStateStoreError,
    StoredLoopRun,
)


LOOP_RUNNER_PACKET_SCHEMA = "skeleton.loop_runner_packet.v1"
LOOP_RUNNER_RESULT_SCHEMA = "skeleton.loop_runner_result.v1"
LOOP_RUNNER_ACTIONS = frozenset({"create", "step"})
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_COMMON_FIELDS = frozenset(
    {
        "schema",
        "action",
        "task_id",
        "run_id",
        "recorded_at",
        "public_safe",
        "no_secrets",
        "no_runtime_mutation",
        "authority_boundary",
    }
)
_STEP_FIELDS = frozenset({"event", "now", "budget_delta", "expected_version"})
_EXPECTED_AUTHORITY_BOUNDARY = {
    "operational_state_write": True,
    "external_side_effects_allowed": False,
    "runtime_mutation_allowed": False,
}


class LoopRunnerAdapterError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def run_loop_task_packet(
    task_packet: object,
    *,
    engine: LoopEngine,
    trusted_recovery_approvals: tuple[Mapping[str, object], ...] = (),
) -> dict[str, object]:
    """Validate one bounded Runner packet and return a public-safe loop receipt."""

    action: object = None
    task_id: object = None
    run_id: object = None
    event_value: object = None

    try:
        if not isinstance(engine, LoopEngine):
            raise LoopRunnerAdapterError("INVALID_LOOP_ENGINE")
        if _looks_like_recovery_packet(task_packet):
            return _run_recovery_packet(
                task_packet,
                engine=engine,
                trusted_recovery_approvals=trusted_recovery_approvals,
            )

        normalized = _validate_packet(task_packet)
        action = normalized["action"]
        task_id = normalized["task_id"]
        run_id = normalized["run_id"]
        event_value = normalized.get("event")

        if action == "create":
            run = engine.create(
                run_id=run_id,
                task_id=task_id,
                recorded_at=normalized["recorded_at"],
            )
            return _receipt_for_run(
                action=action,
                run=run,
                event=None,
                accepted=True,
                decision=LoopDecision.CONTINUE,
                reason="RUN_CREATED",
            )

        current = engine.store.load_run(run_id)
        if current.task_id != task_id:
            raise LoopRunnerAdapterError("LOOP_TASK_ID_MISMATCH")

        event = LoopEvent(normalized["event"])
        step = engine.step(
            run_id=run_id,
            event=event,
            recorded_at=normalized["recorded_at"],
            now=normalized.get("now"),
            budget_delta=normalized.get("budget_delta", 0),
            expected_version=normalized["expected_version"],
        )
        return _receipt_for_step(action=action, step=step)
    except LoopRunnerAdapterError as exc:
        return _blocked_receipt(
            reason=exc.reason_code,
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=event_value,
        )
    except LoopStateConflictError:
        return _blocked_receipt(
            reason="LOOP_STATE_CONFLICT",
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=event_value,
        )
    except LoopStateCorruptionError:
        return _blocked_receipt(
            reason="LOOP_STATE_CORRUPTION",
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=event_value,
        )
    except (LoopStateStoreError, sqlite3.Error):
        return _blocked_receipt(
            reason="LOOP_STATE_STORE_BLOCKED",
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=event_value,
        )
    except (TypeError, ValueError):
        return _blocked_receipt(
            reason="INVALID_LOOP_TASK_PACKET",
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=event_value,
        )


def _looks_like_recovery_packet(task_packet: object) -> bool:
    return (
        isinstance(task_packet, Mapping)
        and task_packet.get("schema") == "skeleton.loop_recovery_packet.v1"
    )


def _run_recovery_packet(
    task_packet: object,
    *,
    engine: LoopEngine,
    trusted_recovery_approvals: tuple[Mapping[str, object], ...],
) -> dict[str, object]:
    action: object = None
    task_id: object = None
    run_id: object = None
    try:
        packet = LoopRecoveryPacket.from_mapping(task_packet)
        action = packet.action
        task_id = packet.task_id
        run_id = packet.run_id
        if resolve_loop_policy_profile(packet.policy_profile) != engine.policy:
            raise LoopRunnerAdapterError("LOOP_RECOVERY_POLICY_MISMATCH")
        approval = _matching_recovery_approval(packet, trusted_recovery_approvals)
        if approval is None:
            raise LoopRunnerAdapterError("LOOP_RECOVERY_APPROVAL_REQUIRED")
        if engine.store.has_recovery_replay(packet.idempotency_key):
            raise LoopRunnerAdapterError("LOOP_RECOVERY_REPLAYED")
        current = engine.store.load_run(packet.run_id)
        if current.task_id != packet.task_id:
            raise LoopRunnerAdapterError("LOOP_TASK_ID_MISMATCH")
        if current.version != packet.expected_version:
            raise LoopStateConflictError("loop recovery expected version conflict")
        if current.context.state is not packet.expected_state:
            raise LoopStateConflictError("loop recovery expected state mismatch")
        result = advance_loop(current.context, packet.event, engine.policy)
        if result.accepted is not True:
            raise LoopRunnerAdapterError("LOOP_RECOVERY_TRANSITION_REJECTED")
        stored = engine.store.append_recovery_result(
            run_id=packet.run_id,
            expected_version=packet.expected_version,
            result=result,
            recorded_at=packet.expected_version + 1,
            idempotency_key=packet.idempotency_key,
            action=packet.action,
            expected_state=packet.expected_state.value,
            policy_profile=packet.policy_profile,
            approval_reference=packet.approval_reference,
            packet_hash=approval.packet_hash,
        )
        return _receipt_for_run(
            action=packet.action,
            run=stored,
            event=packet.event,
            accepted=True,
            decision=result.decision,
            reason=result.reason,
        )
    except LoopRecoveryPacketError as exc:
        return _blocked_receipt(
            reason=exc.reason_code,
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=None,
        )
    except LoopRecoveryApprovalError as exc:
        return _blocked_receipt(
            reason=exc.reason_code,
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=None,
        )
    except LoopRunnerAdapterError as exc:
        return _blocked_receipt(
            reason=exc.reason_code,
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=None,
        )
    except LoopStateConflictError as exc:
        reason = (
            "LOOP_RECOVERY_REPLAYED"
            if "replay conflict" in str(exc)
            else "LOOP_STATE_CONFLICT"
        )
        return _blocked_receipt(
            reason=reason,
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=None,
        )
    except (LoopStateCorruptionError, LoopStateStoreError, sqlite3.Error):
        return _blocked_receipt(
            reason="LOOP_STATE_STORE_BLOCKED",
            action=action,
            task_id=task_id,
            run_id=run_id,
            event=None,
        )


def _matching_recovery_approval(
    packet: LoopRecoveryPacket,
    trusted_recovery_approvals: tuple[Mapping[str, object], ...],
) -> TrustedLoopRecoveryApproval | None:
    expected_hash = loop_recovery_packet_hash(packet)
    for candidate in trusted_recovery_approvals:
        approval = TrustedLoopRecoveryApproval.from_mapping(candidate)
        if approval.packet_hash == expected_hash and approval.matches_packet(packet):
            return approval
    return None


def _validate_packet(task_packet: object) -> dict[str, Any]:
    if not isinstance(task_packet, Mapping):
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")

    action = task_packet.get("action")
    if action not in LOOP_RUNNER_ACTIONS:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")

    allowed_fields = _COMMON_FIELDS | (_STEP_FIELDS if action == "step" else frozenset())
    required_fields = _COMMON_FIELDS | (
        frozenset({"event", "expected_version"}) if action == "step" else frozenset()
    )
    fields = set(task_packet)
    if fields - allowed_fields or required_fields - fields:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    if task_packet.get("schema") != LOOP_RUNNER_PACKET_SCHEMA:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")

    for field in ("public_safe", "no_secrets", "no_runtime_mutation"):
        if task_packet.get(field) is not True:
            raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    if task_packet.get("authority_boundary") != _EXPECTED_AUTHORITY_BOUNDARY:
        raise LoopRunnerAdapterError("INVALID_AUTHORITY_BOUNDARY")

    task_id = _safe_token(task_packet.get("task_id"))
    run_id = _safe_token(task_packet.get("run_id"))
    recorded_at = _non_negative_int(task_packet.get("recorded_at"))

    normalized: dict[str, Any] = {
        "action": action,
        "task_id": task_id,
        "run_id": run_id,
        "recorded_at": recorded_at,
    }
    if action == "create":
        return normalized

    event = task_packet.get("event")
    if not isinstance(event, str):
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    try:
        LoopEvent(event)
    except ValueError as exc:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET") from exc

    normalized["event"] = event
    normalized["expected_version"] = _non_negative_int(task_packet.get("expected_version"))
    if "now" in task_packet:
        normalized["now"] = _non_negative_int(task_packet.get("now"))
    if "budget_delta" in task_packet:
        normalized["budget_delta"] = _non_negative_int(task_packet.get("budget_delta"))
    return normalized


def _receipt_for_step(*, action: str, step: LoopStepResult) -> dict[str, object]:
    return _receipt_for_run(
        action=action,
        run=step.run,
        event=step.transition.event,
        accepted=step.accepted,
        decision=step.decision,
        reason=step.reason,
    )


def _receipt_for_run(
    *,
    action: str,
    run: StoredLoopRun,
    event: LoopEvent | None,
    accepted: bool,
    decision: LoopDecision,
    reason: str,
) -> dict[str, object]:
    status = run.context.state.value if accepted else LoopState.BLOCKED.value
    return {
        "schema": LOOP_RUNNER_RESULT_SCHEMA,
        "status": status,
        "action": action,
        "task_id": run.task_id,
        "run_id": run.run_id,
        "version": run.version,
        "loop_state": run.context.state.value,
        "event": event.value if event is not None else None,
        "accepted": accepted,
        "decision": decision.value,
        "reason": reason,
        "context_hash": run.context_hash,
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _blocked_receipt(
    *,
    reason: str,
    action: object,
    task_id: object,
    run_id: object,
    event: object,
) -> dict[str, object]:
    return {
        "schema": LOOP_RUNNER_RESULT_SCHEMA,
        "status": LoopState.BLOCKED.value,
        "action": _public_identifier(action),
        "task_id": _public_identifier(task_id),
        "run_id": _public_identifier(run_id),
        "version": None,
        "loop_state": LoopState.BLOCKED.value,
        "event": _public_identifier(event),
        "accepted": False,
        "decision": LoopDecision.REJECT.value,
        "reason": reason,
        "context_hash": None,
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _safe_token(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    return value


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    return value


def _public_identifier(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_TOKEN_RE.fullmatch(value):
        return value
    return None
