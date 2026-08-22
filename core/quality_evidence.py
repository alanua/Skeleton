from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
import hashlib
import json
import re


TESTS_GREEN: Final = "TESTS_GREEN"
TASK_SPEC_VALIDATED: Final = "TASK_SPEC_VALIDATED"
PRODUCTION_READY: Final = "PRODUCTION_READY"
ARCHITECTURE_EVALUATOR_REQUIRED: Final = "ARCHITECTURE_EVALUATOR_REQUIRED"
PRODUCTION_CONTRACT_EVIDENCE_REQUIRED: Final = (
    "PRODUCTION_CONTRACT_EVIDENCE_REQUIRED"
)
RUNTIME_PROVEN: Final = "RUNTIME_PROVEN"
POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1: Final = (
    "POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1"
)

READINESS_STATES: Final = frozenset(
    {
        "SPEC_REJECTED",
        TASK_SPEC_VALIDATED,
        TESTS_GREEN,
        ARCHITECTURE_EVALUATOR_REQUIRED,
        PRODUCTION_CONTRACT_EVIDENCE_REQUIRED,
        PRODUCTION_READY,
    }
)

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{16,128}$")
_MAX_COMMANDS = 16
_MAX_COMMAND_LENGTH = 256


class EvidenceValidationError(ValueError):
    """Raised when Phase 1 evidence is malformed or cannot be trusted."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class HeadBoundTestEvidence:
    """Deterministic test evidence bound to an exact base/head pair."""

    base_sha: str
    head_sha: str
    passed: bool
    total_tests: int
    failed_tests: int
    commands: tuple[str, ...]
    evidence_hash: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> HeadBoundTestEvidence:
        if not isinstance(value, Mapping):
            raise EvidenceValidationError(
                "INVALID_TEST_EVIDENCE",
                "test evidence must be an object",
            )
        required = {
            "base_sha",
            "head_sha",
            "passed",
            "total_tests",
            "failed_tests",
            "commands",
            "evidence_hash",
        }
        keys = frozenset(value)
        missing = sorted(required - keys)
        if missing:
            raise EvidenceValidationError(
                "MISSING_TEST_EVIDENCE_FIELD",
                f"missing test evidence field: {missing[0]}",
            )
        unknown = sorted(keys - required)
        if unknown:
            raise EvidenceValidationError(
                "UNKNOWN_TEST_EVIDENCE_FIELD",
                f"unknown test evidence field: {unknown[0]}",
            )
        base_sha = _sha(value["base_sha"], "base_sha")
        head_sha = _sha(value["head_sha"], "head_sha")
        passed = _bool(value["passed"], "passed")
        total_tests = _non_negative_int(value["total_tests"], "total_tests")
        failed_tests = _non_negative_int(value["failed_tests"], "failed_tests")
        if failed_tests > total_tests:
            raise EvidenceValidationError(
                "CONTRADICTORY_TEST_EVIDENCE",
                "failed_tests cannot exceed total_tests",
            )
        if passed and failed_tests != 0:
            raise EvidenceValidationError(
                "CONTRADICTORY_TEST_EVIDENCE",
                "passed evidence cannot include failed tests",
            )
        commands = _commands(value["commands"])
        evidence_hash = _hash(value["evidence_hash"], "evidence_hash")
        return cls(
            base_sha=base_sha,
            head_sha=head_sha,
            passed=passed,
            total_tests=total_tests,
            failed_tests=failed_tests,
            commands=commands,
            evidence_hash=evidence_hash,
        )


@dataclass(frozen=True)
class ProductionContractPreMergePlaceholder:
    """Typed Phase 1 placeholder, not authenticated production evidence."""

    evidence_hash: str
    authenticated: bool = False
    placeholder_kind: str = "PRE_MERGE_PRODUCTION_CONTRACT_PLACEHOLDER"

    def __post_init__(self) -> None:
        if self.authenticated is not False:
            raise EvidenceValidationError(
                "AUTHENTIC_PRODUCTION_CONTRACT_NOT_VERIFIABLE_IN_PHASE1",
                "Phase 1 cannot authenticate production contract evidence",
            )
        if self.placeholder_kind != "PRE_MERGE_PRODUCTION_CONTRACT_PLACEHOLDER":
            raise EvidenceValidationError(
                "INVALID_PRODUCTION_CONTRACT_PLACEHOLDER",
                "production contract placeholder kind is invalid",
            )
        _hash(self.evidence_hash, "evidence_hash")


@dataclass(frozen=True)
class MockProductionContractEvidence:
    evidence_hash: str
    mock_only: bool = True

    def __post_init__(self) -> None:
        _hash(self.evidence_hash, "evidence_hash")
        if self.mock_only is not True:
            raise EvidenceValidationError(
                "INVALID_MOCK_PRODUCTION_CONTRACT_EVIDENCE",
                "mock-only production evidence must remain mock-only",
            )


@dataclass(frozen=True)
class Phase1EvidenceBundle:
    test_evidence: HeadBoundTestEvidence | None = None
    architecture_evidence: object | None = None
    production_contract_evidence: object | None = None
    runtime_evidence: object | None = None


@dataclass(frozen=True)
class ReadinessReceipt:
    state: str
    reason_codes: tuple[str, ...]
    task_spec_valid: bool
    tests_green: bool
    architecture_required: bool
    production_contract_required: bool
    runtime_proven: bool
    scope_classification: str
    privacy_boundary: str
    declared_allowed_files_count: int
    requested_capability_count: int
    validation_requirement_count: int
    declared_allowed_files_hash: str
    requested_capabilities_hash: str
    validation_requirements_hash: str
    base_sha: str
    head_sha: str | None
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.state not in READINESS_STATES:
            raise EvidenceValidationError("INVALID_READINESS_STATE", "invalid state")
        if self.runtime_proven:
            raise EvidenceValidationError(
                POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1,
                "Phase 1 receipts cannot report runtime proof",
            )
        for reason_code in self.reason_codes:
            _token(reason_code, "reason_code")

    @property
    def ready(self) -> bool:
        return self.state == PRODUCTION_READY

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "state": self.state,
            "ready": self.ready,
            "reason_codes": list(self.reason_codes),
            "task_spec_valid": self.task_spec_valid,
            "tests_green": self.tests_green,
            "architecture_required": self.architecture_required,
            "production_contract_required": self.production_contract_required,
            "runtime_proven": False,
            "scope_classification": self.scope_classification,
            "privacy_boundary": self.privacy_boundary,
            "declared_allowed_files_count": self.declared_allowed_files_count,
            "requested_capability_count": self.requested_capability_count,
            "validation_requirement_count": self.validation_requirement_count,
            "declared_allowed_files_hash": self.declared_allowed_files_hash,
            "requested_capabilities_hash": self.requested_capabilities_hash,
            "validation_requirements_hash": self.validation_requirements_hash,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "idempotency_key": self.idempotency_key,
        }


def stable_hash(values: Sequence[str]) -> str:
    payload = json.dumps(list(values), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a 40 character SHA",
        )
    return value.lower()


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a boolean",
        )
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a non-negative integer",
        )
    return value


def _commands(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EvidenceValidationError(
            "INVALID_TEST_COMMANDS",
            "test commands must be a sequence of strings",
        )
    if not value or len(value) > _MAX_COMMANDS:
        raise EvidenceValidationError(
            "INVALID_TEST_COMMANDS",
            "test commands must be non-empty and bounded",
        )
    commands: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise EvidenceValidationError(
                "INVALID_TEST_COMMAND",
                "test command must be a non-empty string",
            )
        command = item.strip()
        if len(command) > _MAX_COMMAND_LENGTH:
            raise EvidenceValidationError(
                "INVALID_TEST_COMMAND",
                "test command is too long",
            )
        commands.append(command)
    return tuple(commands)


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a stable hexadecimal hash",
        )
    return value.lower()


def _token(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a stable token",
        )
    return value
