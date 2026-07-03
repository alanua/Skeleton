from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from core.loop_controller import LoopDecision, LoopEvent, LoopState
from core.loop_engine import LoopEngine, LoopStepResult
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
    task_packet: Mapping[str, Any],
    *,
    engine: LoopEngine,
) -> dict[str, object]:
    """Validate one bounded Runner packet and return a public-safe loop receipt."""

    action = _public_value(task_packet, "action")
    task_id = _public_value(task_packet, "task_id")
    run_id = _public_value(task_packet, "run_id")
    event_value = _public_value(task_packet, "event")

    try:
        if not isinstance(engine, LoopEngine):
            raise LoopRunnerAdapterError("INVALID_LOOP_ENGINE")
        normalized = _validate_packet(task_packet)
        action = normalized["action"]
        task_id = normalized["task_id"]
        run_id = normalized["run_id"]

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


def _validate_packet(task_packet: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(task_packet, Mapping):
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")

    action = task_packet.get("action")
    allowed_fields = _COMMON_FIELDS | (_STEP_FIELDS if action == "step" else frozenset())
    if set(task_packet) - allowed_fields:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    if set(_COMMON_FIELDS) - set(task_packet):
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    if task_packet.get("schema") != LOOP_RUNNER_PACKET_SCHEMA:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    if action not in LOOP_RUNNER_ACTIONS:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")

    task_id = _safe_token(task_packet.get("task_id"))
    run_id = _safe_token(task_packet.get("run_id"))
    recorded_at = _non_negative_int(task_packet.get("recorded_at"))

    for field in ("public_safe", "no_secrets", "no_runtime_mutation"):
        if task_packet.get(field) is not True:
            raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    if task_packet.get("authority_boundary") != _EXPECTED_AUTHORITY_BOUNDARY:
        raise LoopRunnerAdapterError("INVALID_AUTHORITY_BOUNDARY")

    normalized: dict[str, Any] = {
        "action": action,
        "task_id": task_id,
        "run_id": run_id,
        "recorded_at": recorded_at,
    }
    if action == "create":
        return normalized

    if not _STEP_FIELDS.issuperset(set(task_packet) - _COMMON_FIELDS):
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
    if "event" not in task_packet or "expected_version" not in task_packet:
        raise LoopRunnerAdapterError("INVALID_LOOP_TASK_PACKET")
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


def _public_value(packet: object, field: str) -> object:
    if isinstance(packet, Mapping):
        return packet.get(field)
    return None


def _public_identifier(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_TOKEN_RE.fullmatch(value):
        return value
    return None
