from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.runner_vnext_compat import CompatError, LegacyTaskObservation, adapt_legacy_task
from core.runner_vnext_authority import AUTHORITATIVE_MODE
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_shadow import DivergenceKind, ShadowParityHarness

VNEXT_MODE_OFF = "off"
VNEXT_MODE_SHADOW = "shadow"
VNEXT_MODE_GREEN_CANARY = "green_canary"
VNEXT_MODE_AUTHORITATIVE = AUTHORITATIVE_MODE
VNEXT_MODES = frozenset({
    VNEXT_MODE_OFF,
    VNEXT_MODE_SHADOW,
    VNEXT_MODE_GREEN_CANARY,
    VNEXT_MODE_AUTHORITATIVE,
})


class VNextCutoverBridgeError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class VNextCutoverBridgeDecision:
    mode: str
    status: str
    divergence: str | None
    vnext_effect_class: str | None
    reason_code: str
    canary_eligible: bool
    allow_legacy_execution: bool
    source_task_ref: str | None = None
    target_state_ref: str | None = None
    side_effects_executed: bool = False

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "schema": "skeleton.runner_vnext_cutover_bridge.v1",
            "mode": self.mode,
            "status": self.status,
            "divergence": self.divergence,
            "vnext_effect_class": self.vnext_effect_class,
            "reason_code": self.reason_code,
            "canary_eligible": self.canary_eligible,
            "allow_legacy_execution": self.allow_legacy_execution,
            "source_task_ref": self.source_task_ref,
            "target_state_ref": self.target_state_ref,
            "side_effects_executed": self.side_effects_executed,
        }


def evaluate_vnext_cutover_bridge(
    *,
    configured_mode: str | None,
    normalized_metadata: Mapping[str, object],
) -> VNextCutoverBridgeDecision:
    mode = _mode(configured_mode)
    if mode == VNEXT_MODE_OFF:
        return VNextCutoverBridgeDecision(
            mode=mode,
            status="off",
            divergence=None,
            vnext_effect_class=None,
            reason_code="VNEXT_CUTOVER_OFF",
            canary_eligible=False,
            allow_legacy_execution=True,
        )

    observation = _observation(normalized_metadata)
    receipt = ShadowParityHarness().evaluate(observation)
    canary_eligible = _canary_eligible(observation)

    if mode == VNEXT_MODE_SHADOW:
        return VNextCutoverBridgeDecision(
            mode=mode,
            status="shadow_observed",
            divergence=receipt.divergence.value,
            vnext_effect_class=(receipt.vnext_effect_class.value if receipt.vnext_effect_class else None),
            reason_code=receipt.vnext_reason_code,
            canary_eligible=canary_eligible,
            allow_legacy_execution=True,
            source_task_ref=receipt.source_task_ref,
            target_state_ref=receipt.target_state_ref,
        )

    if mode == VNEXT_MODE_AUTHORITATIVE:
        if not _authoritative_eligible(observation):
            return VNextCutoverBridgeDecision(
                mode=mode,
                status="authoritative_block",
                divergence=receipt.divergence.value,
                vnext_effect_class=(receipt.vnext_effect_class.value if receipt.vnext_effect_class else None),
                reason_code="VNEXT_AUTHORITATIVE_NOT_ELIGIBLE",
                canary_eligible=False,
                allow_legacy_execution=False,
                source_task_ref=receipt.source_task_ref,
                target_state_ref=receipt.target_state_ref,
            )
        allowed = (
            receipt.divergence is DivergenceKind.MATCH
            and receipt.vnext_effect_class is EffectClass.GREEN
            and receipt.legacy_effect_class is EffectClass.GREEN
        )
        return VNextCutoverBridgeDecision(
            mode=mode,
            status="authoritative_green_ready" if allowed else "authoritative_block",
            divergence=receipt.divergence.value,
            vnext_effect_class=(receipt.vnext_effect_class.value if receipt.vnext_effect_class else None),
            reason_code=("VNEXT_AUTHORITATIVE_GREEN_READY" if allowed else receipt.vnext_reason_code),
            canary_eligible=True,
            allow_legacy_execution=False,
            source_task_ref=receipt.source_task_ref,
            target_state_ref=receipt.target_state_ref,
        )

    if not canary_eligible:
        return VNextCutoverBridgeDecision(
            mode=mode,
            status="legacy_protected_path",
            divergence=receipt.divergence.value,
            vnext_effect_class=(receipt.vnext_effect_class.value if receipt.vnext_effect_class else None),
            reason_code="VNEXT_CANARY_NOT_ELIGIBLE",
            canary_eligible=False,
            allow_legacy_execution=True,
            source_task_ref=receipt.source_task_ref,
            target_state_ref=receipt.target_state_ref,
        )

    allowed = (
        receipt.divergence is DivergenceKind.MATCH
        and receipt.vnext_effect_class is EffectClass.GREEN
        and receipt.legacy_effect_class is EffectClass.GREEN
    )
    return VNextCutoverBridgeDecision(
        mode=mode,
        status="green_canary_pass" if allowed else "green_canary_block",
        divergence=receipt.divergence.value,
        vnext_effect_class=(receipt.vnext_effect_class.value if receipt.vnext_effect_class else None),
        reason_code=("VNEXT_GREEN_CANARY_MATCH" if allowed else receipt.vnext_reason_code),
        canary_eligible=True,
        allow_legacy_execution=allowed,
        source_task_ref=receipt.source_task_ref,
        target_state_ref=receipt.target_state_ref,
    )


def _mode(value: str | None) -> str:
    if value is None or not value.strip():
        return VNEXT_MODE_OFF
    mode = value.strip().lower()
    if mode not in VNEXT_MODES:
        raise VNextCutoverBridgeError("VNEXT_CUTOVER_MODE_INVALID")
    return mode


def _observation(metadata: Mapping[str, object]) -> LegacyTaskObservation:
    issue_number = metadata.get("issue_number")
    if not isinstance(issue_number, int) or issue_number < 1:
        raise VNextCutoverBridgeError("VNEXT_METADATA_ISSUE_INVALID")
    repo = metadata.get("repo")
    branch = metadata.get("branch")
    base_sha = metadata.get("base_sha")
    route = metadata.get("legacy_route")
    if not isinstance(repo, str) or not isinstance(branch, str):
        raise VNextCutoverBridgeError("VNEXT_METADATA_REPOSITORY_INVALID")
    if not isinstance(base_sha, str) or len(base_sha) != 40:
        raise VNextCutoverBridgeError("VNEXT_METADATA_BASE_SHA_REQUIRED")

    task_kind = "code_edit" if route == "code_generation" else "publish" if route == "publish_only" else "diagnostic"
    legacy_effect = EffectClass.GREEN if task_kind in {"code_edit", "diagnostic"} else EffectClass.YELLOW
    privacy = metadata.get("privacy_boundary")
    privacy_boundary = _privacy_boundary(privacy)
    capabilities = _string_tuple(metadata.get("requested_capabilities"))
    if not capabilities and task_kind == "code_edit":
        capabilities = ("repository_write_allowlisted", "test_execution")
    allowed_files = _string_tuple(metadata.get("allowed_files"))
    if not allowed_files:
        raise VNextCutoverBridgeError("VNEXT_METADATA_ALLOWED_FILES_REQUIRED")

    return LegacyTaskObservation(
        source_task_ref=f"issue:{issue_number}",
        target_state_ref=f"git:{base_sha.lower()}",
        repo=repo,
        branch=branch,
        task_kind=task_kind,
        requested_capabilities=capabilities,
        allowed_files=allowed_files,
        privacy_boundary=privacy_boundary,
        legacy_effect_class=legacy_effect,
        legacy_status="READY",
        evidence_refs=(),
    )


def _canary_eligible(observation: LegacyTaskObservation) -> bool:
    if observation.task_kind != "code_edit":
        return False
    try:
        adapted = adapt_legacy_task(observation)
    except CompatError:
        return False
    return (
        adapted.task.privacy is PrivacyClass.PUBLIC_SAFE
        and all(resource.startswith("repo:") for resource in adapted.operation.resources)
    )


def _authoritative_eligible(observation: LegacyTaskObservation) -> bool:
    if not _canary_eligible(observation):
        return False
    return observation.requested_capabilities == (
        "repository_read",
        "repository_write_allowlisted",
        "test_execution",
    )


def _privacy_boundary(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VNextCutoverBridgeError("VNEXT_METADATA_PRIVACY_REQUIRED")
    normalized = value.strip().upper()
    if normalized in {"PUBLIC_SAFE_REPOSITORY_ONLY", "PUBLIC_SAFE_AGGREGATE_ONLY", "PUBLIC_SAFE_CODE_AND_TESTS_ONLY", "PUBLIC_SAFE"}:
        return "PUBLIC_SAFE_REPOSITORY_ONLY"
    if normalized in {"LOCAL_PRIVATE", "PRIVATE_LOCAL", "PRIVATE", "PRIVATE_LOCAL_ONLY", "PRIVATE_MEMORY"}:
        return "PRIVATE"
    raise VNextCutoverBridgeError("VNEXT_METADATA_PRIVACY_UNSUPPORTED")


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return tuple(item for item in value if item)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(item for item in value if item)
    return ()
