from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
import json
import re

from core.quality_evidence import (
    ARCHITECTURE_EVALUATOR_REQUIRED,
    POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1,
    PRODUCTION_CONTRACT_EVIDENCE_REQUIRED,
    PRODUCTION_READY,
    TASK_SPEC_VALIDATED,
    TESTS_GREEN,
    HeadBoundTestEvidence,
    MockProductionContractEvidence,
    Phase1EvidenceBundle,
    ProductionContractPreMergePlaceholder,
    ReadinessReceipt,
    stable_hash,
)


TASK_SPEC_SCHEMA: Final = "skeleton.task_spec.v1"
TASK_KINDS: Final = frozenset(
    {
        "code_generation",
        "code_edit",
        "repository_maintenance",
        "diagnostic",
        "review",
        "publish",
    }
)
RISK_LEVELS: Final = frozenset({"low", "medium", "high", "critical"})
PRIVACY_BOUNDARIES: Final = frozenset(
    {
        "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "PUBLIC_SAFE_REPOSITORY_ONLY",
        "PUBLIC_SAFE_AGGREGATE_ONLY",
    }
)
REQUESTED_CAPABILITIES: Final = frozenset(
    {
        "repository_read",
        "repository_write",
        "repository_write_allowlisted",
        "test_execution",
        "publish_pull_request",
    }
)
EVIDENCE_EXPECTATIONS: Final = frozenset(
    {
        "deterministic_tests",
        "architecture_invariant_proof",
        "production_contract_premerge_placeholder",
        "postmerge_runtime_proof",
    }
)

PROTECTED_SCOPE_CLASSIFICATION: Final = "protected-policy-change-required"
UNPROTECTED_SCOPE_CLASSIFICATION: Final = "non-protected-phase1-scope"

_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/@+-]{0,511}$")
_MAX_ITEMS = 128
_MAX_TEXT = 512

_REQUIRED_FIELDS: Final = frozenset(
    {
        "schema",
        "repo",
        "base_sha",
        "branch",
        "task_kind",
        "risk",
        "protected_intent",
        "requested_capabilities",
        "allowed_files",
        "forbidden_actions",
        "validation_requirements",
        "expected_output",
        "privacy_boundary",
        "dependencies",
        "evidence_expectations",
        "idempotency_key",
    }
)

_PROTECTED_PATHS: Final = (
    "BOOT_MANIFEST.yaml",
    "PROJECT_TREE.yaml",
    "OPERATOR_RULES.yaml",
    "CAPABILITY_REGISTRY.yaml",
    "INVARIANTS.yaml",
    ".github/workflows",
    "scripts/runner_poll_github_tasks.py",
    "core/gate_engine.py",
    "core/action_gate.py",
    "core/architecture_invariants.py",
    "secrets",
    "deploy",
    "server",
    "finance",
    "legal",
    "governance",
    "Runner_core",
    "adapter_boundaries",
)


class TaskSpecValidationError(ValueError):
    """Raised when normalized Phase 1 task formulation fails closed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class TaskSpec:
    schema: str
    repo: str
    base_sha: str
    branch: str
    task_kind: str
    risk: str
    protected_intent: bool
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    validation_requirements: tuple[str, ...]
    expected_output: tuple[str, ...]
    privacy_boundary: str
    dependencies: tuple[str, ...]
    evidence_expectations: tuple[str, ...]
    idempotency_key: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> TaskSpec:
        if not isinstance(value, Mapping):
            raise TaskSpecValidationError(
                "INVALID_TASK_SPEC",
                "TaskSpec must be an object",
            )
        if any(not isinstance(key, str) for key in value):
            raise TaskSpecValidationError(
                "INVALID_TASK_SPEC_FIELD",
                "TaskSpec keys must be strings",
            )
        keys = frozenset(value)
        missing = sorted(_REQUIRED_FIELDS - keys)
        if missing:
            raise TaskSpecValidationError(
                "MATERIAL_INCOMPLETE_TASK_SPEC",
                f"missing TaskSpec field: {missing[0]}",
            )
        unknown = sorted(keys - _REQUIRED_FIELDS)
        if unknown:
            raise TaskSpecValidationError(
                "UNKNOWN_TASK_SPEC_FIELD",
                f"unknown TaskSpec field: {unknown[0]}",
            )

        spec = cls(
            schema=_exact(value["schema"], "schema", TASK_SPEC_SCHEMA),
            repo=_repository(value["repo"]),
            base_sha=_sha(value["base_sha"], "base_sha"),
            branch=_branch(value["branch"]),
            task_kind=_enum(value["task_kind"], "task_kind", TASK_KINDS),
            risk=_enum(value["risk"], "risk", RISK_LEVELS),
            protected_intent=_bool(value["protected_intent"], "protected_intent"),
            requested_capabilities=_unique_enum_items(
                value["requested_capabilities"],
                "requested_capabilities",
                REQUESTED_CAPABILITIES,
            ),
            allowed_files=_allowed_files(value["allowed_files"]),
            forbidden_actions=_text_items(value["forbidden_actions"], "forbidden_actions"),
            validation_requirements=_text_items(
                value["validation_requirements"],
                "validation_requirements",
            ),
            expected_output=_text_items(value["expected_output"], "expected_output"),
            privacy_boundary=_enum(
                value["privacy_boundary"],
                "privacy_boundary",
                PRIVACY_BOUNDARIES,
            ),
            dependencies=_text_items(value["dependencies"], "dependencies", allow_empty=True),
            evidence_expectations=_unique_enum_items(
                value["evidence_expectations"],
                "evidence_expectations",
                EVIDENCE_EXPECTATIONS,
                allow_empty=True,
            ),
            idempotency_key=_token(value["idempotency_key"], "idempotency_key"),
        )
        _validate_consistency(spec)
        return spec

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "repo": self.repo,
            "base_sha": self.base_sha,
            "branch": self.branch,
            "task_kind": self.task_kind,
            "risk": self.risk,
            "protected_intent": self.protected_intent,
            "requested_capabilities": list(self.requested_capabilities),
            "allowed_files": list(self.allowed_files),
            "forbidden_actions": list(self.forbidden_actions),
            "validation_requirements": list(self.validation_requirements),
            "expected_output": list(self.expected_output),
            "privacy_boundary": self.privacy_boundary,
            "dependencies": list(self.dependencies),
            "evidence_expectations": list(self.evidence_expectations),
            "idempotency_key": self.idempotency_key,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_mapping(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def scope_classification(self) -> str:
        if self.protected_intent or any(_is_protected_path(path) for path in self.allowed_files):
            return PROTECTED_SCOPE_CLASSIFICATION
        return UNPROTECTED_SCOPE_CLASSIFICATION


@dataclass(frozen=True)
class Phase1ReadinessConfig:
    current_head_sha: str
    require_architecture_evidence: bool = False
    require_production_contract_proof: bool = False

    def __post_init__(self) -> None:
        _sha(self.current_head_sha, "current_head_sha")
        _bool(self.require_architecture_evidence, "require_architecture_evidence")
        _bool(
            self.require_production_contract_proof,
            "require_production_contract_proof",
        )


def evaluate_phase1_readiness(
    task_spec: object,
    *,
    evidence: Phase1EvidenceBundle | None = None,
    config: Phase1ReadinessConfig,
) -> ReadinessReceipt:
    evidence = evidence or Phase1EvidenceBundle()
    if not isinstance(config, Phase1ReadinessConfig):
        raise TypeError("config must be Phase1ReadinessConfig")

    try:
        spec = task_spec if isinstance(task_spec, TaskSpec) else TaskSpec.from_mapping(task_spec)  # type: ignore[arg-type]
    except TaskSpecValidationError as exc:
        return _receipt_for_rejected_spec(config.current_head_sha, exc.reason_code)

    reasons: list[str] = []
    state = TASK_SPEC_VALIDATED
    tests_green = False

    if evidence.runtime_evidence is not None:
        reasons.append(POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1)

    test_evidence = evidence.test_evidence
    if test_evidence is None:
        reasons.append("TEST_EVIDENCE_REQUIRED")
    elif not isinstance(test_evidence, HeadBoundTestEvidence):
        reasons.append("INVALID_TEST_EVIDENCE")
    elif test_evidence.base_sha != spec.base_sha:
        reasons.append("TEST_EVIDENCE_BASE_SHA_MISMATCH")
    elif test_evidence.head_sha != config.current_head_sha:
        reasons.append("HEAD_MOVED_INVALIDATES_EVIDENCE")
    elif not test_evidence.passed:
        reasons.append("TEST_EVIDENCE_NOT_GREEN")
    else:
        state = TESTS_GREEN
        tests_green = True

    if config.require_architecture_evidence:
        reasons.append(ARCHITECTURE_EVALUATOR_REQUIRED)
        return _receipt(
            spec,
            state=state,
            reason_codes=reasons,
            tests_green=tests_green,
            architecture_required=True,
            production_contract_required=config.require_production_contract_proof,
            head_sha=_head_sha(test_evidence),
        )

    if config.require_production_contract_proof:
        production_evidence = evidence.production_contract_evidence
        if isinstance(production_evidence, MockProductionContractEvidence):
            reasons.append("MOCK_PRODUCTION_CONTRACT_EVIDENCE_REJECTED")
        elif isinstance(production_evidence, ProductionContractPreMergePlaceholder):
            reasons.append("PRODUCTION_CONTRACT_AUTHENTICITY_PENDING_PHASE_3153")
        else:
            reasons.append(PRODUCTION_CONTRACT_EVIDENCE_REQUIRED)
        return _receipt(
            spec,
            state=state,
            reason_codes=reasons,
            tests_green=tests_green,
            architecture_required=False,
            production_contract_required=True,
            head_sha=_head_sha(test_evidence),
        )

    if (
        tests_green
        and not reasons
        and spec.risk == "low"
        and spec.scope_classification == UNPROTECTED_SCOPE_CLASSIFICATION
    ):
        state = PRODUCTION_READY
    elif spec.scope_classification == PROTECTED_SCOPE_CLASSIFICATION:
        reasons.append("PROTECTED_POLICY_CHANGE_REQUIRED")
    elif spec.risk in {"high", "critical"}:
        reasons.append("PROTECTED_READINESS_REVIEW_REQUIRED")

    return _receipt(
        spec,
        state=state,
        reason_codes=reasons,
        tests_green=tests_green,
        architecture_required=False,
        production_contract_required=False,
        head_sha=_head_sha(test_evidence),
    )


def _receipt_for_rejected_spec(head_sha: str, reason_code: str) -> ReadinessReceipt:
    return ReadinessReceipt(
        state="SPEC_REJECTED",
        reason_codes=(reason_code,),
        task_spec_valid=False,
        tests_green=False,
        architecture_required=False,
        production_contract_required=False,
        runtime_proven=False,
        scope_classification="unknown",
        privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY",
        declared_allowed_files_count=0,
        requested_capability_count=0,
        validation_requirement_count=0,
        declared_allowed_files_hash=stable_hash(()),
        requested_capabilities_hash=stable_hash(()),
        validation_requirements_hash=stable_hash(()),
        base_sha="0" * 40,
        head_sha=head_sha,
        idempotency_key="invalid-task-spec",
    )


def _receipt(
    spec: TaskSpec,
    *,
    state: str,
    reason_codes: Sequence[str],
    tests_green: bool,
    architecture_required: bool,
    production_contract_required: bool,
    head_sha: str | None,
) -> ReadinessReceipt:
    return ReadinessReceipt(
        state=state,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        task_spec_valid=True,
        tests_green=tests_green,
        architecture_required=architecture_required,
        production_contract_required=production_contract_required,
        runtime_proven=False,
        scope_classification=spec.scope_classification,
        privacy_boundary=spec.privacy_boundary,
        declared_allowed_files_count=len(spec.allowed_files),
        requested_capability_count=len(spec.requested_capabilities),
        validation_requirement_count=len(spec.validation_requirements),
        declared_allowed_files_hash=stable_hash(spec.allowed_files),
        requested_capabilities_hash=stable_hash(spec.requested_capabilities),
        validation_requirements_hash=stable_hash(spec.validation_requirements),
        base_sha=spec.base_sha,
        head_sha=head_sha,
        idempotency_key=spec.idempotency_key,
    )


def _head_sha(test_evidence: object) -> str | None:
    if isinstance(test_evidence, HeadBoundTestEvidence):
        return test_evidence.head_sha
    return None


def _validate_consistency(spec: TaskSpec) -> None:
    if spec.task_kind in {"code_generation", "code_edit"}:
        if "repository_read" not in spec.requested_capabilities:
            raise TaskSpecValidationError(
                "CONTRADICTORY_TASK_SPEC",
                "code tasks require repository_read",
            )
        write_caps = {"repository_write", "repository_write_allowlisted"}
        if not write_caps.intersection(spec.requested_capabilities):
            raise TaskSpecValidationError(
                "CONTRADICTORY_TASK_SPEC",
                "code tasks require an explicit repository write capability",
            )
        if not spec.allowed_files:
            raise TaskSpecValidationError(
                "MATERIAL_INCOMPLETE_TASK_SPEC",
                "code tasks require declared file scope",
            )
    if "test_execution" in spec.requested_capabilities and not spec.validation_requirements:
        raise TaskSpecValidationError(
            "CONTRADICTORY_TASK_SPEC",
            "test execution requires validation requirements",
        )
    if (
        "postmerge_runtime_proof" in spec.evidence_expectations
        and spec.privacy_boundary != "PUBLIC_SAFE_POLICY_METADATA_ONLY"
    ):
        raise TaskSpecValidationError(
            "CONTRADICTORY_TASK_SPEC",
            "runtime proof expectations must stay in public-safe metadata",
        )


def _exact(value: object, field: str, expected: str) -> str:
    if value != expected:
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must equal {expected}",
        )
    return expected


def _repository(value: object) -> str:
    if not isinstance(value, str) or not _REPOSITORY_RE.fullmatch(value):
        raise TaskSpecValidationError(
            "INVALID_REPOSITORY",
            "repo must be owner/name",
        )
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a 40 character SHA",
        )
    return value.lower()


def _branch(value: object) -> str:
    if not isinstance(value, str) or not _BRANCH_RE.fullmatch(value):
        raise TaskSpecValidationError("INVALID_BRANCH", "branch is malformed")
    if "/./" in value or "/../" in value or "//" in value or value.endswith(("/", ".lock")):
        raise TaskSpecValidationError("INVALID_BRANCH", "branch is unsafe")
    return value


def _enum(value: object, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} is not allowed",
        )
    return value


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a boolean",
        )
    return value


def _token(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a stable token",
        )
    return value


def _unique_enum_items(
    value: object,
    field: str,
    allowed: frozenset[str],
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    items = _sequence(value, field, allow_empty=allow_empty)
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str) or item not in allowed:
            raise TaskSpecValidationError(
                f"INVALID_{field.upper().rstrip('S')}",
                f"{field} contains an invalid item",
            )
        normalized.append(item)
    if len(set(normalized)) != len(normalized):
        raise TaskSpecValidationError(
            f"DUPLICATE_{field.upper()}",
            f"{field} contains duplicates",
        )
    return tuple(sorted(normalized))


def _text_items(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    items = _sequence(value, field, allow_empty=allow_empty)
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip() or len(item) > _MAX_TEXT:
            raise TaskSpecValidationError(
                f"INVALID_{field.upper().rstrip('S')}",
                f"{field} contains an invalid item",
            )
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise TaskSpecValidationError(
            f"DUPLICATE_{field.upper()}",
            f"{field} contains duplicates",
        )
    return tuple(sorted(normalized))


def _allowed_files(value: object) -> tuple[str, ...]:
    items = _sequence(value, "allowed_files")
    paths: list[str] = []
    for item in items:
        if not isinstance(item, str) or not _SAFE_PATH_RE.fullmatch(item):
            raise TaskSpecValidationError(
                "INVALID_DECLARED_ALLOWED_FILE",
                "declared allowed file is malformed",
            )
        if item in {".", ".."} or item.endswith("/") or "//" in item:
            raise TaskSpecValidationError(
                "INVALID_DECLARED_ALLOWED_FILE",
                "declared allowed file is unsafe",
            )
        if any(part in {".", ".."} for part in item.split("/")) and not item.startswith(".github/"):
            raise TaskSpecValidationError(
                "INVALID_DECLARED_ALLOWED_FILE",
                "declared allowed file is unsafe",
            )
        paths.append(item)
    if len(set(paths)) != len(paths):
        raise TaskSpecValidationError(
            "DUPLICATE_DECLARED_ALLOWED_FILES",
            "declared allowed files contain duplicates",
        )
    return tuple(sorted(paths))


def _sequence(value: object, field: str, *, allow_empty: bool = False) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a sequence",
        )
    if (not allow_empty and not value) or len(value) > _MAX_ITEMS:
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be non-empty and bounded",
        )
    return value


def _is_protected_path(path: str) -> bool:
    return any(path == protected or path.startswith(protected + "/") for protected in _PROTECTED_PATHS)
