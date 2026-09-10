from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from core.runner_vnext_contracts import EffectClass, GREEN_EFFECTS, RED_EFFECTS, YELLOW_EFFECTS


class RecoveryCommand(str, Enum):
    REFRESH_TRUST_ANCHOR_BUNDLE = "refresh_trust_anchor_bundle"
    POLLER_RELOAD_AND_STALE_REACTIVATION = "poller_reload_and_stale_reactivation"
    CHECKOUT_FRESHNESS_CANARY = "checkout_freshness_canary"
    TYPED_REPAIR_RUNTIME_STATE = "typed_repair_runtime_state"
    QUEUE_FENCE_RESET = "queue_fence_reset"


COMMAND_EFFECT_CEILING = {
    RecoveryCommand.REFRESH_TRUST_ANCHOR_BUNDLE: EffectClass.YELLOW,
    RecoveryCommand.POLLER_RELOAD_AND_STALE_REACTIVATION: EffectClass.YELLOW,
    RecoveryCommand.CHECKOUT_FRESHNESS_CANARY: EffectClass.GREEN,
    RecoveryCommand.TYPED_REPAIR_RUNTIME_STATE: EffectClass.YELLOW,
    RecoveryCommand.QUEUE_FENCE_RESET: EffectClass.YELLOW,
}


class RecoveryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RecoveryRequest:
    request_id: str
    command: RecoveryCommand
    target_state_ref: str
    idempotency_key: str
    bounded_resources: tuple[str, ...]
    expected_effects: tuple[str, ...]
    approval_evidence: tuple[str, ...] = ()
    current_fence_token: int | None = None


@dataclass(frozen=True)
class RecoveryContext:
    current_target_state_ref: str
    current_fence_token: int | None = None


@dataclass(frozen=True)
class RecoveryHandlerResult:
    reason_code: str
    touched_resource_refs: tuple[str, ...]
    before_state_ref: str
    after_state_ref: str
    validation_status: str


@dataclass(frozen=True)
class RecoveryReceipt:
    request_id: str
    command: RecoveryCommand
    idempotency_key: str
    status: str
    reason_code: str
    effect_ceiling: EffectClass
    touched_resource_refs: tuple[str, ...]
    before_state_ref: str
    after_state_ref: str
    validation_status: str


Handler = Callable[[RecoveryRequest, RecoveryContext], RecoveryHandlerResult]


class RecoveryKernel:
    """Closed-command recovery kernel with no shell/subprocess escape path."""

    def __init__(self, handlers: Mapping[RecoveryCommand, Handler]) -> None:
        unknown = set(handlers) - set(RecoveryCommand)
        if unknown:
            raise RecoveryError("UNKNOWN_RECOVERY_HANDLER")
        self._handlers = dict(handlers)

    def execute(self, request: RecoveryRequest, context: RecoveryContext) -> RecoveryReceipt:
        if request.target_state_ref != context.current_target_state_ref:
            raise RecoveryError("RECOVERY_TARGET_STATE_MISMATCH")
        if not request.idempotency_key or not request.request_id:
            raise RecoveryError("RECOVERY_IDENTITY_REQUIRED")
        if not request.bounded_resources:
            raise RecoveryError("RECOVERY_BOUNDED_RESOURCES_REQUIRED")
        _validate_effect_ceiling(request)
        if request.command is RecoveryCommand.QUEUE_FENCE_RESET:
            if request.current_fence_token is None or context.current_fence_token is None:
                raise RecoveryError("RECOVERY_FENCE_EVIDENCE_REQUIRED")
            if request.current_fence_token != context.current_fence_token:
                raise RecoveryError("RECOVERY_STALE_FENCE_EVIDENCE")
        handler = self._handlers.get(request.command)
        if handler is None:
            raise RecoveryError("RECOVERY_HANDLER_UNAVAILABLE")
        result = handler(request, context)
        _validate_public_refs(request.bounded_resources)
        _validate_public_refs(result.touched_resource_refs)
        _validate_public_refs((result.before_state_ref, result.after_state_ref))
        if not set(result.touched_resource_refs).issubset(set(request.bounded_resources)):
            raise RecoveryError("RECOVERY_RESOURCE_SCOPE_EXCEEDED")
        _validate_reason_code(result.reason_code)
        if result.validation_status not in {"PASS", "FAIL"}:
            raise RecoveryError("RECOVERY_VALIDATION_STATUS_INVALID")
        return RecoveryReceipt(
            request_id=request.request_id,
            command=request.command,
            idempotency_key=request.idempotency_key,
            status="DONE" if result.validation_status == "PASS" else "FAILED",
            reason_code=result.reason_code,
            effect_ceiling=COMMAND_EFFECT_CEILING[request.command],
            touched_resource_refs=result.touched_resource_refs,
            before_state_ref=result.before_state_ref,
            after_state_ref=result.after_state_ref,
            validation_status=result.validation_status,
        )


def _validate_public_refs(refs: tuple[str, ...]) -> None:
    for ref in refs:
        if not ref or ref.startswith(("/", "~")) or "\\" in ref or ".." in ref:
            raise RecoveryError("RECOVERY_PUBLIC_REF_REQUIRED")
        if ":" not in ref:
            raise RecoveryError("RECOVERY_PUBLIC_REF_REQUIRED")


def _validate_effect_ceiling(request: RecoveryRequest) -> None:
    effects = frozenset(request.expected_effects)
    known = GREEN_EFFECTS | YELLOW_EFFECTS | RED_EFFECTS
    if not effects or effects - known:
        raise RecoveryError("RECOVERY_EFFECT_UNKNOWN")
    if effects & RED_EFFECTS:
        raise RecoveryError("RECOVERY_RED_EFFECT_PROHIBITED")
    ceiling = COMMAND_EFFECT_CEILING[request.command]
    if ceiling is EffectClass.GREEN and effects & YELLOW_EFFECTS:
        raise RecoveryError("RECOVERY_EFFECT_CEILING_EXCEEDED")


def _validate_reason_code(value: str) -> None:
    if not value or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in value):
        raise RecoveryError("RECOVERY_REASON_CODE_INVALID")
