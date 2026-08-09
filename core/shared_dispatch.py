from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Final

from core.control_recovery import (
    ROUTE_CONTROL_RECOVERY,
    RecoveryStore,
    execute_recovery_packet,
)
from core.loop_controller import LoopPolicy
from core.loop_engine import LoopEngine
from core.loop_runner_adapter import run_loop_task_packet
from core.loop_state_store import LoopStateStore


DISPATCH_RECEIPT_SCHEMA: Final = "skeleton.scheduler_dispatch_receipt.v1"
ROUTE_LOOP_ENGINE_PACKET: Final = "loop_engine_packet"
ROUTE_CONTROL_RECOVERY_PACKET: Final = ROUTE_CONTROL_RECOVERY
PRIVACY_PUBLIC_SAFE: Final = "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY"


class SharedDispatchError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SharedDispatchRequest:
    occurrence_id: str
    route_type: str
    route_id: str
    payload: Mapping[str, Any]
    attempt: int
    idempotency_key: str
    parent_receipt_id: str | None = None


@dataclass(frozen=True)
class SharedDispatchResult:
    status: str
    reason: str
    receipt: Mapping[str, Any]
    evidence_ref: str
    retryable: bool = False
    waiting_dependency: str | None = None
    next_step: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DispatchRoute:
    route_type: str
    route_id: str
    required_capabilities: frozenset[str]
    handler: Callable[[SharedDispatchRequest], Mapping[str, Any]]
    packet_key: str = "task_packet"


class SharedDispatcher:
    """Fail-closed typed dispatch boundary for scheduler/continuation work."""

    def __init__(self, routes: Mapping[tuple[str, str], DispatchRoute]) -> None:
        self._routes = dict(routes)

    @classmethod
    def for_loop_engine(cls, *, loop_state_db_path: str) -> "SharedDispatcher":
        def handler(request: SharedDispatchRequest) -> Mapping[str, Any]:
            task_packet = request.payload.get("task_packet")
            store = LoopStateStore(loop_state_db_path)
            store.initialize()
            engine = LoopEngine(store, LoopPolicy())
            return run_loop_task_packet(task_packet, engine=engine)

        route = DispatchRoute(
            route_type="loop",
            route_id=ROUTE_LOOP_ENGINE_PACKET,
            required_capabilities=frozenset({"loop:state_write"}),
            handler=handler,
        )
        return cls({("loop", ROUTE_LOOP_ENGINE_PACKET): route})

    @classmethod
    def for_control_recovery(
        cls,
        *,
        recovery_db_path: str,
        action_executor: Callable[[str], str],
        canary_executor: Callable[[str], bool] | None = None,
        now: int | None = None,
    ) -> "SharedDispatcher":
        def handler(request: SharedDispatchRequest) -> Mapping[str, Any]:
            store = RecoveryStore(recovery_db_path)
            return execute_recovery_packet(
                request.payload.get("recovery_packet", {}),
                store=store,
                now=request.attempt if now is None else now,
                action_executor=action_executor,
                canary_executor=canary_executor,
            )

        route = DispatchRoute(
            route_type="workflow",
            route_id=ROUTE_CONTROL_RECOVERY_PACKET,
            required_capabilities=frozenset({"control:recovery"}),
            handler=handler,
            packet_key="recovery_packet",
        )
        return cls({("workflow", ROUTE_CONTROL_RECOVERY_PACKET): route})

    def dispatch(self, request: SharedDispatchRequest) -> SharedDispatchResult:
        try:
            route = self._validate_request(request)
            receipt = route.handler(request)
            return self._result_from_receipt(request, receipt)
        except SharedDispatchError as exc:
            return _blocked_result(request, exc.reason_code)
        except Exception:
            return _blocked_result(request, "DISPATCH_HANDLER_RAISED", retryable=True)

    def _validate_request(self, request: SharedDispatchRequest) -> DispatchRoute:
        if not isinstance(request, SharedDispatchRequest):
            raise SharedDispatchError("INVALID_DISPATCH_REQUEST")
        if request.attempt <= 0:
            raise SharedDispatchError("INVALID_ATTEMPT")
        route = self._routes.get((request.route_type, request.route_id))
        if route is None:
            raise SharedDispatchError("ROUTE_NOT_ALLOWLISTED")
        _validate_policy_envelope(request.payload, route.required_capabilities, route.packet_key)
        return route

    def _result_from_receipt(
        self, request: SharedDispatchRequest, receipt: Mapping[str, Any]
    ) -> SharedDispatchResult:
        if not isinstance(receipt, Mapping):
            return _blocked_result(request, "DISPATCH_RECEIPT_SCHEMA_MISMATCH")
        reason = _safe_reason(receipt.get("reason"), default="DISPATCH_RESULT")
        evidence_ref = _evidence_ref(request, receipt)
        status = str(receipt.get("status") or "")
        accepted = receipt.get("accepted") is True
        decision = receipt.get("decision")

        if status == "WAITING_RECOVERY":
            return SharedDispatchResult(
                "failed",
                reason,
                _wrap_receipt(request, receipt),
                evidence_ref,
                retryable=True,
            )
        if not accepted or status in {"BLOCKED", "failed", "FAILED"} or decision == "REJECT":
            retryable = reason in {
                "LOOP_STATE_CONFLICT",
                "LOOP_STATE_STORE_BLOCKED",
                "DISPATCH_HANDLER_RAISED",
            }
            return SharedDispatchResult(
                "failed",
                reason,
                _wrap_receipt(request, receipt),
                evidence_ref,
                retryable=retryable,
            )
        if decision in {"ESCALATE", "REVIEW"} or status in {"NEEDS_OPERATOR", "HUMAN_REVIEW"}:
            return SharedDispatchResult(
                "needs_operator", reason, _wrap_receipt(request, receipt), evidence_ref
            )

        next_step = _next_step(request.payload)
        return SharedDispatchResult(
            "done",
            reason,
            _wrap_receipt(request, receipt),
            evidence_ref,
            next_step=next_step,
        )


def _validate_policy_envelope(
    payload: Mapping[str, Any], required_capabilities: frozenset[str], packet_key: str
) -> None:
    if not isinstance(payload, Mapping):
        raise SharedDispatchError("INVALID_DISPATCH_PAYLOAD")
    if payload.get("privacy_boundary") != PRIVACY_PUBLIC_SAFE:
        raise SharedDispatchError("PRIVACY_BOUNDARY_MISMATCH")
    if payload.get("bounded") is not True:
        raise SharedDispatchError("UNBOUNDED_DISPATCH_PAYLOAD")
    approved = payload.get("approved_capabilities")
    if not isinstance(approved, list) or any(not isinstance(item, str) for item in approved):
        raise SharedDispatchError("INVALID_APPROVED_CAPABILITIES")
    approved_set = frozenset(approved)
    if not required_capabilities.issubset(approved_set):
        raise SharedDispatchError("CAPABILITY_NOT_APPROVED")
    requested = payload.get("requested_capabilities", [])
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise SharedDispatchError("INVALID_REQUESTED_CAPABILITIES")
    if not frozenset(requested).issubset(approved_set):
        raise SharedDispatchError("CAPABILITY_NOT_APPROVED")
    if packet_key not in payload:
        raise SharedDispatchError("MISSING_TYPED_TASK_PACKET")


def _next_step(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    workflow = payload.get("deterministic_workflow")
    if not isinstance(workflow, Mapping):
        return None
    steps = workflow.get("steps")
    index = workflow.get("index")
    if (
        not isinstance(steps, list)
        or not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or index + 1 >= len(steps)
    ):
        return None
    candidate = steps[index + 1]
    if not isinstance(candidate, Mapping):
        return None
    next_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"task_packet", "deterministic_workflow", "wait_for"}
    }
    next_payload["task_packet"] = dict(candidate)
    next_payload["deterministic_workflow"] = {"steps": steps, "index": index + 1}
    return next_payload


def _wrap_receipt(
    request: SharedDispatchRequest, route_receipt: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema": DISPATCH_RECEIPT_SCHEMA,
        "occurrence_id": request.occurrence_id,
        "route_type": request.route_type,
        "route_id": request.route_id,
        "attempt": request.attempt,
        "idempotency_key": request.idempotency_key,
        "parent_receipt_id": request.parent_receipt_id,
        "route_receipt": dict(route_receipt),
        "public_safe": True,
        "external_side_effects_executed": False,
    }


def _blocked_result(
    request: SharedDispatchRequest, reason: str, *, retryable: bool = False
) -> SharedDispatchResult:
    receipt = _wrap_receipt(
        request,
        {
            "status": "BLOCKED",
            "accepted": False,
            "decision": "REJECT",
            "reason": reason,
            "public_safe": True,
            "external_side_effects_executed": False,
        },
    )
    return SharedDispatchResult(
        "failed", reason, receipt, _evidence_ref(request, receipt), retryable=retryable
    )


def _evidence_ref(request: SharedDispatchRequest, receipt: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"idempotency_key": request.idempotency_key, "receipt": dict(receipt)},
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "dispatch:" + hashlib.sha256(encoded).hexdigest()


def _safe_reason(value: object, *, default: str) -> str:
    if isinstance(value, str) and value and len(value) <= 128:
        return value
    return default
