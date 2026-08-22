from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ImpactLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    PROTECTED = "protected"


_ORDER = {
    ImpactLevel.GREEN: 0,
    ImpactLevel.YELLOW: 1,
    ImpactLevel.RED: 2,
    ImpactLevel.PROTECTED: 3,
}


def max_impact(*levels: ImpactLevel) -> ImpactLevel:
    if not levels:
        return ImpactLevel.GREEN
    return max(levels, key=lambda level: _ORDER[level])


@dataclass(frozen=True, slots=True)
class PredictedImpact:
    level: ImpactLevel
    reasons: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    privacy_boundary: str


@dataclass(frozen=True, slots=True)
class ObservedDiffImpact:
    level: ImpactLevel
    reasons: tuple[str, ...]
    changed_files: tuple[str, ...]
    protected_files: tuple[str, ...] = ()
    mutating_files: tuple[str, ...] = ()
    production_files: tuple[str, ...] = ()


def classify_capability_impact(
    *,
    requested_capabilities: tuple[str, ...],
    allowed_files: tuple[str, ...],
    privacy_boundary: str,
) -> PredictedImpact:
    reasons: list[str] = []
    level = ImpactLevel.GREEN
    capabilities = set(requested_capabilities)
    if "repository_write" in capabilities or "repository_write_allowlisted" in capabilities:
        level = max_impact(level, ImpactLevel.YELLOW)
        reasons.append("repository_write_requested")
    if "test_execution" in capabilities:
        reasons.append("test_execution_requested")
    if privacy_boundary.startswith("PRIVATE"):
        level = max_impact(level, ImpactLevel.RED)
        reasons.append("private_boundary")
    if any(_path_is_protected(path) for path in allowed_files):
        level = max_impact(level, ImpactLevel.PROTECTED)
        reasons.append("protected_scope_allowed")
    return PredictedImpact(
        level=level,
        reasons=tuple(sorted(set(reasons))),
        requested_capabilities=tuple(sorted(capabilities)),
        allowed_files=tuple(sorted(allowed_files)),
        privacy_boundary=privacy_boundary,
    )


def classify_path_impact(path: str) -> ImpactLevel:
    if _path_is_protected(path):
        return ImpactLevel.PROTECTED
    if _path_is_production(path) or _path_is_mutating(path):
        return ImpactLevel.RED
    if path.startswith("tests/") or path.startswith("docs/"):
        return ImpactLevel.GREEN
    return ImpactLevel.YELLOW


def _path_is_protected(path: str) -> bool:
    protected_names = (
        "BOOT_MANIFEST",
        "OPERATOR_RULES",
        "CAPABILITY_REGISTRY",
        "INVARIANTS",
    )
    return path.startswith(".github/workflows/") or any(name in path for name in protected_names)


def _path_is_mutating(path: str) -> bool:
    return any(
        marker in path
        for marker in (
            "action_gate",
            "gate_engine",
            "merge",
            "deploy",
            "runtime",
            "executor",
            "credential",
            "secret",
            "provider",
            "device",
        )
    )


def _path_is_production(path: str) -> bool:
    return path.startswith(("core/", "scripts/", "ops/", "android/home/app/src/main/"))
