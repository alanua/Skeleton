from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Final, Mapping


QUALITY_EVIDENCE_SCHEMA: Final = "skeleton.quality_evidence.v1"


class EvidenceKind(Enum):
    TEST = "TEST"
    ARCHITECTURE = "ARCHITECTURE"
    REVIEW = "REVIEW"
    DEPENDENCY = "DEPENDENCY"
    RUNTIME = "RUNTIME"


class EvidenceStrength(Enum):
    NONE = "NONE"
    MOCK_ONLY = "MOCK_ONLY"
    STATIC_CONTRACT = "STATIC_CONTRACT"
    PRODUCTION_CONTRACT = "PRODUCTION_CONTRACT"
    POST_RUNTIME = "POST_RUNTIME"


class RuntimeEvidenceState(Enum):
    NOT_PROVIDED = "NOT_PROVIDED"
    PRE_MERGE_ONLY = "PRE_MERGE_ONLY"
    POST_MERGE_CANARY_GREEN = "POST_MERGE_CANARY_GREEN"


@dataclass(frozen=True)
class TestEvidence:
    tests_passed: bool
    diff_check_passed: bool
    compile_passed: bool
    strength: EvidenceStrength = EvidenceStrength.STATIC_CONTRACT
    production_contract_required: bool = False
    bound_head_sha: str | None = None

    @property
    def is_green(self) -> bool:
        if not (self.tests_passed and self.diff_check_passed and self.compile_passed):
            return False
        if self.production_contract_required:
            return self.strength is EvidenceStrength.PRODUCTION_CONTRACT
        return self.strength in {
            EvidenceStrength.MOCK_ONLY,
            EvidenceStrength.STATIC_CONTRACT,
            EvidenceStrength.PRODUCTION_CONTRACT,
        }


@dataclass(frozen=True)
class DependencyEvidence:
    declared_dependencies: tuple[str, ...] = ()
    existing_dependencies: tuple[str, ...] = ()
    missing_allowed: bool = False

    @property
    def missing_dependencies(self) -> tuple[str, ...]:
        existing = set(self.existing_dependencies)
        return tuple(sorted(dependency for dependency in self.declared_dependencies if dependency not in existing))

    @property
    def is_satisfied(self) -> bool:
        return self.missing_allowed or not self.missing_dependencies


@dataclass(frozen=True)
class ReviewEvidence:
    independent_review: bool = False
    adversarial_review: bool = False
    bound_head_sha: str | None = None
    protected_review_required: bool = False

    @property
    def satisfies_architecture_review(self) -> bool:
        return self.independent_review and self.adversarial_review


@dataclass(frozen=True)
class RuntimeEvidence:
    state: RuntimeEvidenceState = RuntimeEvidenceState.NOT_PROVIDED
    bound_head_sha: str | None = None

    @property
    def is_runtime_proven(self) -> bool:
        return self.state is RuntimeEvidenceState.POST_MERGE_CANARY_GREEN


@dataclass(frozen=True)
class EvidenceBundle:
    tests: TestEvidence | None = None
    dependencies: DependencyEvidence | None = None
    review: ReviewEvidence | None = None
    runtime: RuntimeEvidence | None = None


def evidence_bundle_from_mapping(value: Mapping[str, Any]) -> EvidenceBundle:
    """Build an immutable evidence bundle from plain public-safe metadata."""

    if not isinstance(value, Mapping):
        raise ValueError("INVALID_EVIDENCE_BUNDLE")
    return EvidenceBundle(
        tests=_test_evidence(value.get("tests")),
        dependencies=_dependency_evidence(value.get("dependencies")),
        review=_review_evidence(value.get("review")),
        runtime=_runtime_evidence(value.get("runtime")),
    )


def evidence_receipt(
    *,
    reason_codes: tuple[str, ...],
    tests: TestEvidence | None,
    dependencies: DependencyEvidence | None,
    review: ReviewEvidence | None,
    runtime: RuntimeEvidence | None,
) -> dict[str, object]:
    """Return only enums, booleans, counts, and hashes suitable for public logs."""

    return {
        "schema": QUALITY_EVIDENCE_SCHEMA,
        "reason_codes": tuple(reason_codes),
        "tests_present": tests is not None,
        "tests_green": bool(tests and tests.is_green),
        "test_strength": tests.strength.value if tests else EvidenceStrength.NONE.value,
        "production_contract_required": bool(tests and tests.production_contract_required),
        "dependency_count": len(dependencies.declared_dependencies) if dependencies else 0,
        "missing_dependency_count": len(dependencies.missing_dependencies) if dependencies else 0,
        "dependency_set_hash": stable_public_hash(dependencies.declared_dependencies if dependencies else ()),
        "independent_review": bool(review and review.independent_review),
        "adversarial_review": bool(review and review.adversarial_review),
        "protected_review_required": bool(review and review.protected_review_required),
        "runtime_state": runtime.state.value if runtime else RuntimeEvidenceState.NOT_PROVIDED.value,
    }


def stable_public_hash(values: tuple[str, ...] | list[str]) -> str:
    normalized = tuple(sorted(str(value) for value in values))
    encoded = json.dumps(normalized, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_full_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _test_evidence(value: object) -> TestEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("INVALID_TEST_EVIDENCE")
    strength = _enum(value.get("strength", EvidenceStrength.STATIC_CONTRACT.value), EvidenceStrength, "INVALID_TEST_STRENGTH")
    bound_head_sha = value.get("bound_head_sha")
    if bound_head_sha is not None and not is_full_sha(bound_head_sha):
        raise ValueError("INVALID_EVIDENCE_SHA")
    return TestEvidence(
        tests_passed=_bool(value.get("tests_passed"), "INVALID_TEST_EVIDENCE"),
        diff_check_passed=_bool(value.get("diff_check_passed"), "INVALID_TEST_EVIDENCE"),
        compile_passed=_bool(value.get("compile_passed"), "INVALID_TEST_EVIDENCE"),
        strength=strength,
        production_contract_required=_bool(value.get("production_contract_required", False), "INVALID_TEST_EVIDENCE"),
        bound_head_sha=bound_head_sha.lower() if isinstance(bound_head_sha, str) else None,
    )


def _dependency_evidence(value: object) -> DependencyEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("INVALID_DEPENDENCY_EVIDENCE")
    return DependencyEvidence(
        declared_dependencies=_string_tuple(value.get("declared_dependencies", ()), "INVALID_DEPENDENCY_EVIDENCE"),
        existing_dependencies=_string_tuple(value.get("existing_dependencies", ()), "INVALID_DEPENDENCY_EVIDENCE"),
        missing_allowed=_bool(value.get("missing_allowed", False), "INVALID_DEPENDENCY_EVIDENCE"),
    )


def _review_evidence(value: object) -> ReviewEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("INVALID_REVIEW_EVIDENCE")
    bound_head_sha = value.get("bound_head_sha")
    if bound_head_sha is not None and not is_full_sha(bound_head_sha):
        raise ValueError("INVALID_EVIDENCE_SHA")
    return ReviewEvidence(
        independent_review=_bool(value.get("independent_review", False), "INVALID_REVIEW_EVIDENCE"),
        adversarial_review=_bool(value.get("adversarial_review", False), "INVALID_REVIEW_EVIDENCE"),
        bound_head_sha=bound_head_sha.lower() if isinstance(bound_head_sha, str) else None,
        protected_review_required=_bool(value.get("protected_review_required", False), "INVALID_REVIEW_EVIDENCE"),
    )


def _runtime_evidence(value: object) -> RuntimeEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("INVALID_RUNTIME_EVIDENCE")
    bound_head_sha = value.get("bound_head_sha")
    if bound_head_sha is not None and not is_full_sha(bound_head_sha):
        raise ValueError("INVALID_EVIDENCE_SHA")
    return RuntimeEvidence(
        state=_enum(value.get("state", RuntimeEvidenceState.NOT_PROVIDED.value), RuntimeEvidenceState, "INVALID_RUNTIME_STATE"),
        bound_head_sha=bound_head_sha.lower() if isinstance(bound_head_sha, str) else None,
    )


def _enum(value: object, enum_type: type[Enum], code: str) -> Any:
    if not isinstance(value, str):
        raise ValueError(code)
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(code) from exc


def _bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(code)
    return value


def _string_tuple(value: object, code: str) -> tuple[str, ...]:
    if isinstance(value, tuple):
        items = value
    elif isinstance(value, list):
        items = tuple(value)
    else:
        raise ValueError(code)
    if any(not isinstance(item, str) or not item for item in items):
        raise ValueError(code)
    return tuple(sorted(set(items)))
