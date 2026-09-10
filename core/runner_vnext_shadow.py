from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.runner_vnext_compat import CompatError, LegacyTaskObservation, adapt_legacy_task
from core.runner_vnext_contracts import EffectClass, classify_policy


class DivergenceKind(str, Enum):
    MATCH = "MATCH"
    VNEXT_STRICTER = "VNEXT_STRICTER"
    LEGACY_STRICTER = "LEGACY_STRICTER"
    INCOMPARABLE = "INCOMPARABLE"
    INVALID_LEGACY_INPUT = "INVALID_LEGACY_INPUT"


@dataclass(frozen=True)
class ShadowParityReceipt:
    source_task_ref: str
    target_state_ref: str
    legacy_effect_class: EffectClass | None
    legacy_status: str
    vnext_effect_class: EffectClass | None
    vnext_reason_code: str
    divergence: DivergenceKind
    evidence_refs: tuple[str, ...]
    side_effects_executed: bool = False


_RANK = {EffectClass.GREEN: 0, EffectClass.YELLOW: 1, EffectClass.RED: 2}


class ShadowParityHarness:
    """Pure planning/parity harness. It has no executor, subprocess, label or publication path."""

    def evaluate(self, observation: LegacyTaskObservation) -> ShadowParityReceipt:
        try:
            adapted = adapt_legacy_task(observation)
        except CompatError as exc:
            return ShadowParityReceipt(
                source_task_ref=_safe_ref_or_redacted(observation.source_task_ref, "source"),
                target_state_ref=_safe_ref_or_redacted(observation.target_state_ref, "target"),
                legacy_effect_class=observation.legacy_effect_class,
                legacy_status=_safe_status_or_redacted(observation.legacy_status),
                vnext_effect_class=None,
                vnext_reason_code=exc.reason_code,
                divergence=DivergenceKind.INVALID_LEGACY_INPUT,
                evidence_refs=tuple(ref for ref in observation.evidence_refs if _is_public_safe_ref(ref)),
            )
        decision = classify_policy(adapted.policy_input)
        return ShadowParityReceipt(
            source_task_ref=observation.source_task_ref,
            target_state_ref=observation.target_state_ref,
            legacy_effect_class=observation.legacy_effect_class,
            legacy_status=observation.legacy_status,
            vnext_effect_class=decision.effect_class,
            vnext_reason_code=decision.reason_code,
            divergence=_compare(observation.legacy_effect_class, decision.effect_class),
            evidence_refs=observation.evidence_refs,
        )


def _compare(legacy: EffectClass | None, vnext: EffectClass) -> DivergenceKind:
    if legacy is None:
        return DivergenceKind.INCOMPARABLE
    if legacy is vnext:
        return DivergenceKind.MATCH
    if _RANK[vnext] > _RANK[legacy]:
        return DivergenceKind.VNEXT_STRICTER
    return DivergenceKind.LEGACY_STRICTER



def _is_public_safe_ref(value: str) -> bool:
    return bool(value) and not value.startswith(("/", "~")) and "\\" not in value and ".." not in value and ":" in value


def _safe_ref_or_redacted(value: str, kind: str) -> str:
    return value if _is_public_safe_ref(value) else f"redacted:invalid-{kind}-ref"


def _safe_status_or_redacted(value: str) -> str:
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
    return value if value and all(ch in allowed for ch in value) else "redacted:invalid-legacy-status"
