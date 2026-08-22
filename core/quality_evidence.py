from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Final

from core.architecture_invariants import (
    ArchitectureInvariantResult,
    ArchitectureStatus,
    EvidenceBinding,
    EvidenceKind,
)
from core.task_quality_gate import TaskSpecValidation, TaskSpecStatus


_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_REVISION_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


class ReadinessState(str, Enum):
    NOT_READY = "NOT_READY"
    TASK_SPEC_ACCEPTED = "TASK_SPEC_ACCEPTED"
    TESTS_GREEN = "TESTS_GREEN"
    ARCHITECTURE_GREEN = "ARCHITECTURE_GREEN"
    PRODUCTION_READY = "PRODUCTION_READY"
    RUNTIME_PROVEN = "RUNTIME_PROVEN"


class EvidenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ProbeStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class TestEvidence:
    binding: EvidenceBinding
    status: EvidenceStatus
    command_count: int


@dataclass(frozen=True)
class ProductionContractEvidence:
    binding: EvidenceBinding
    status: EvidenceStatus
    kind: EvidenceKind
    contract_id: str


@dataclass(frozen=True)
class RuntimeEvidence:
    repository: str
    base_sha: str
    reviewed_head_sha: str
    merged_main_sha: str
    runtime_sha: str | None
    immutable_runtime_revision: str | None
    canary_status: ProbeStatus
    probe_status: ProbeStatus
    evidence_id: str


@dataclass(frozen=True)
class ReadinessConfig:
    architecture_required: bool
    real_production_contract_required: bool


@dataclass(frozen=True)
class ReadinessResult:
    state: ReadinessState
    reason_codes: tuple[str, ...]
    receipt: MappingProxyType[str, object]


def evaluate_readiness(
    *,
    task_validation: TaskSpecValidation,
    test_evidence: TestEvidence | None,
    architecture_result: ArchitectureInvariantResult | None,
    production_contract_evidence: ProductionContractEvidence | None,
    runtime_evidence: object | None,
    config: ReadinessConfig,
    repository: str,
    base_sha: str,
    head_sha: str,
    current_head_sha: str | None = None,
    merged: bool = False,
) -> ReadinessResult:
    reasons: list[str] = []
    state = ReadinessState.NOT_READY

    normalized_base = _sha(base_sha, reasons, "INVALID_BASE_SHA")
    normalized_head = _sha(head_sha, reasons, "INVALID_HEAD_SHA")
    normalized_current = (
        _sha(current_head_sha, reasons, "INVALID_CURRENT_HEAD_SHA")
        if current_head_sha is not None
        else normalized_head
    )
    if reasons:
        return _result(state, reasons, repository, normalized_base, normalized_head)
    if normalized_current != normalized_head:
        reasons.append("HEAD_MOVED")
        return _result(state, reasons, repository, normalized_base, normalized_head)

    if task_validation.status is not TaskSpecStatus.ACCEPTED:
        reasons.append("TASK_SPEC_REJECTED")
        reasons.extend(task_validation.reason_codes)
        return _result(state, reasons, repository, normalized_base, normalized_head)
    state = ReadinessState.TASK_SPEC_ACCEPTED

    if not _tests_green(test_evidence, repository, normalized_base, normalized_head, reasons):
        return _result(state, reasons, repository, normalized_base, normalized_head)
    state = ReadinessState.TESTS_GREEN

    if config.architecture_required:
        if architecture_result is None:
            reasons.append("MISSING_ARCHITECTURE_RESULT")
            return _result(state, reasons, repository, normalized_base, normalized_head)
        if architecture_result.status is not ArchitectureStatus.GREEN:
            reasons.append("ARCHITECTURE_NOT_GREEN")
            reasons.extend(architecture_result.reason_codes)
            return _result(state, reasons, repository, normalized_base, normalized_head)
        state = ReadinessState.ARCHITECTURE_GREEN

    if not _production_contract_green(
        production_contract_evidence,
        config,
        repository,
        normalized_base,
        normalized_head,
        reasons,
    ):
        return _result(state, reasons, repository, normalized_base, normalized_head)
    state = ReadinessState.PRODUCTION_READY

    if not merged:
        return _result(state, reasons, repository, normalized_base, normalized_head)
    if not _runtime_proven(
        runtime_evidence,
        repository,
        normalized_base,
        normalized_head,
        reasons,
    ):
        return _result(state, reasons, repository, normalized_base, normalized_head)
    return _result(
        ReadinessState.RUNTIME_PROVEN,
        reasons,
        repository,
        normalized_base,
        normalized_head,
    )


def _tests_green(
    evidence: TestEvidence | None,
    repository: str,
    base_sha: str,
    head_sha: str,
    reasons: list[str],
) -> bool:
    if evidence is None:
        reasons.append("MISSING_TEST_EVIDENCE")
        return False
    if not isinstance(evidence, TestEvidence):
        reasons.append("MALFORMED_TEST_EVIDENCE")
        return False
    _binding_matches(evidence.binding, repository, base_sha, head_sha, reasons)
    if evidence.status is not EvidenceStatus.PASS:
        reasons.append("TESTS_NOT_GREEN")
    if evidence.command_count <= 0:
        reasons.append("MISSING_TEST_COMMANDS")
    return not reasons


def _production_contract_green(
    evidence: ProductionContractEvidence | None,
    config: ReadinessConfig,
    repository: str,
    base_sha: str,
    head_sha: str,
    reasons: list[str],
) -> bool:
    if evidence is None:
        reasons.append("MISSING_PRODUCTION_CONTRACT_EVIDENCE")
        return False
    if not isinstance(evidence, ProductionContractEvidence):
        reasons.append("MALFORMED_PRODUCTION_CONTRACT_EVIDENCE")
        return False
    _binding_matches(evidence.binding, repository, base_sha, head_sha, reasons)
    if evidence.status is not EvidenceStatus.PASS:
        reasons.append("PRODUCTION_CONTRACT_NOT_GREEN")
    if not evidence.contract_id:
        reasons.append("MISSING_PRODUCTION_CONTRACT_ID")
    if config.real_production_contract_required and evidence.kind is EvidenceKind.MOCK:
        reasons.append("MOCK_ONLY_PRODUCTION_CONTRACT_EVIDENCE")
    return not reasons


def _runtime_proven(
    evidence: object | None,
    repository: str,
    base_sha: str,
    head_sha: str,
    reasons: list[str],
) -> bool:
    if not isinstance(evidence, RuntimeEvidence):
        reasons.append("STRUCTURED_RUNTIME_EVIDENCE_REQUIRED")
        return False
    if evidence.repository != repository:
        reasons.append("RUNTIME_REPOSITORY_MISMATCH")
    if evidence.base_sha != base_sha:
        reasons.append("RUNTIME_BASE_SHA_MISMATCH")
    if evidence.reviewed_head_sha != head_sha:
        reasons.append("RUNTIME_REVIEWED_HEAD_SHA_MISMATCH")
    if not _valid_sha(evidence.merged_main_sha):
        reasons.append("INVALID_MERGED_MAIN_SHA")
    if evidence.runtime_sha is None and evidence.immutable_runtime_revision is None:
        reasons.append("MISSING_RUNTIME_IDENTITY")
    if evidence.runtime_sha is not None and not _valid_sha(evidence.runtime_sha):
        reasons.append("INVALID_RUNTIME_SHA")
    if (
        evidence.immutable_runtime_revision is not None
        and not _REVISION_RE.fullmatch(evidence.immutable_runtime_revision)
    ):
        reasons.append("INVALID_RUNTIME_REVISION")
    if evidence.runtime_sha is not None and evidence.runtime_sha != evidence.merged_main_sha:
        reasons.append("RUNTIME_SHA_MERGE_MISMATCH")
    if evidence.canary_status is not ProbeStatus.SUCCESS:
        reasons.append("POST_MERGE_CANARY_NOT_GREEN")
    if evidence.probe_status is not ProbeStatus.SUCCESS:
        reasons.append("POST_MERGE_PROBE_NOT_GREEN")
    if not evidence.evidence_id:
        reasons.append("MISSING_RUNTIME_EVIDENCE_ID")
    return not reasons


def _binding_matches(
    binding: EvidenceBinding,
    repository: str,
    base_sha: str,
    head_sha: str,
    reasons: list[str],
) -> None:
    if binding.repository != repository:
        reasons.append("EVIDENCE_REPOSITORY_MISMATCH")
    if binding.base_sha != base_sha:
        reasons.append("EVIDENCE_BASE_SHA_MISMATCH")
    if binding.head_sha != head_sha:
        reasons.append("EVIDENCE_HEAD_SHA_MISMATCH")


def _result(
    state: ReadinessState,
    reasons: list[str],
    repository: str,
    base_sha: str | None,
    head_sha: str | None,
) -> ReadinessResult:
    reason_codes = tuple(dict.fromkeys(reasons))
    return ReadinessResult(
        state=state,
        reason_codes=reason_codes,
        receipt=MappingProxyType(
            {
                "state": state.value,
                "reason_codes": reason_codes,
                "repository_id": repository,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "ready": state
                in {ReadinessState.PRODUCTION_READY, ReadinessState.RUNTIME_PROVEN},
                "runtime_proven": state is ReadinessState.RUNTIME_PROVEN,
            }
        ),
    )


def _sha(value: object, reasons: list[str], code: str) -> str | None:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        reasons.append(code)
        return None
    return value.lower()


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None
