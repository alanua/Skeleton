from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Final


TASK_SPEC_SCHEMA: Final = "skeleton.task_spec.v1"
TASK_KINDS: Final = frozenset(
    {
        "code_generation",
        "bug_fix",
        "test_generation",
        "documentation",
        "review",
        "diagnostic",
    }
)
RISK_LEVELS: Final = frozenset({"low", "medium", "high", "protected"})
PROTECTED_INTENTS: Final = frozenset(
    {
        "none",
        "runtime-change",
        "protected-policy-change-required",
        "protected-runtime-change-required",
    }
)
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
        "diagnostic_read",
        "documentation_write",
    }
)

_REQUIRED_FIELDS: Final = frozenset(
    {
        "schema",
        "repository",
        "base",
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
    }
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/@+-]{0,511}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PROTECTED_POLICY_PATHS: Final = frozenset(
    {
        "BOOT_MANIFEST.yaml",
        "PROJECT_TREE.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        "INVARIANTS.yaml",
        "core/gate_engine.py",
        "core/action_gate.py",
    }
)
_PROTECTED_PREFIXES: Final = (".github/workflows/", "policies/", "governance/")


class TaskSpecStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class PredictedRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    PROTECTED = "PROTECTED"


@dataclass(frozen=True)
class BaseBinding:
    ref: str
    sha: str


@dataclass(frozen=True)
class ValidationRequirements:
    commands: tuple[tuple[str, ...], ...]
    requires_architecture_evidence: bool
    requires_real_production_contract: bool


@dataclass(frozen=True)
class EvidenceExpectations:
    tests: bool
    architecture: bool
    production_contract: bool
    runtime: bool


@dataclass(frozen=True)
class DeclaredScope:
    allowed_files: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    forbidden_actions_count: int


@dataclass(frozen=True)
class PredictedProfile:
    risk: PredictedRisk
    protected_intent: str
    declared_scope: DeclaredScope
    predicted_impact: tuple[str, ...]


@dataclass(frozen=True)
class TaskSpec:
    schema: str
    repository: str
    base: BaseBinding
    branch: str
    task_kind: str
    risk: str
    protected_intent: str
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    validation_requirements: ValidationRequirements
    expected_output: tuple[str, ...]
    privacy_boundary: str
    dependencies: tuple[str, ...]
    evidence_expectations: EvidenceExpectations

    @property
    def declared_scope(self) -> DeclaredScope:
        return DeclaredScope(
            allowed_files=self.allowed_files,
            requested_capabilities=self.requested_capabilities,
            forbidden_actions_count=len(self.forbidden_actions),
        )

    def predicted_profile(self) -> PredictedProfile:
        impact = ["declared_scope_review"]
        if self.validation_requirements.requires_architecture_evidence:
            impact.append("architecture_evidence_required")
        if self.validation_requirements.requires_real_production_contract:
            impact.append("real_production_contract_required")
        if self.protected_intent != "none":
            impact.append(self.protected_intent)
        return PredictedProfile(
            risk=PredictedRisk(self.risk.upper()),
            protected_intent=self.protected_intent,
            declared_scope=self.declared_scope,
            predicted_impact=tuple(sorted(impact)),
        )


@dataclass(frozen=True)
class TaskSpecValidation:
    status: TaskSpecStatus
    reason_codes: tuple[str, ...]
    task_spec: TaskSpec | None = None
    predicted_profile: PredictedProfile | None = None

    @property
    def accepted(self) -> bool:
        return self.status is TaskSpecStatus.ACCEPTED


def validate_task_spec(value: object) -> TaskSpecValidation:
    reasons: list[str] = []
    spec = _normalize_task_spec(value, reasons)
    if spec is None:
        return TaskSpecValidation(
            status=TaskSpecStatus.REJECTED,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )
    return TaskSpecValidation(
        status=TaskSpecStatus.ACCEPTED,
        reason_codes=(),
        task_spec=spec,
        predicted_profile=spec.predicted_profile(),
    )


def _normalize_task_spec(value: object, reasons: list[str]) -> TaskSpec | None:
    if not isinstance(value, Mapping):
        reasons.append("INVALID_TASK_SPEC")
        return None
    if any(not isinstance(key, str) for key in value):
        reasons.append("INVALID_FIELD_NAME")
        return None

    keys = frozenset(value)
    for field in sorted(_REQUIRED_FIELDS - keys):
        reasons.append(f"MISSING_{field.upper()}")
    for field in sorted(keys - _REQUIRED_FIELDS):
        reasons.append(f"UNKNOWN_{field.upper()}")
    if reasons:
        return None

    schema = _exact(value["schema"], TASK_SPEC_SCHEMA, "INVALID_SCHEMA", reasons)
    repository = _regex(value["repository"], _REPOSITORY_RE, "INVALID_REPOSITORY", reasons)
    base = _base(value["base"], reasons)
    branch = _regex(value["branch"], _BRANCH_RE, "INVALID_BRANCH", reasons)
    task_kind = _enum(value["task_kind"], TASK_KINDS, "INVALID_TASK_KIND", reasons)
    risk = _enum(value["risk"], RISK_LEVELS, "INVALID_RISK", reasons)
    protected_intent = _enum(
        value["protected_intent"],
        PROTECTED_INTENTS,
        "INVALID_PROTECTED_INTENT",
        reasons,
    )
    requested_capabilities = _string_tuple(
        value["requested_capabilities"],
        "INVALID_REQUESTED_CAPABILITIES",
        reasons,
        allowed=REQUESTED_CAPABILITIES,
        sort=True,
    )
    allowed_files = _string_tuple(
        value["allowed_files"],
        "INVALID_ALLOWED_FILES",
        reasons,
        pattern=_PATH_RE,
        sort=True,
    )
    forbidden_actions = _string_tuple(
        value["forbidden_actions"],
        "INVALID_FORBIDDEN_ACTIONS",
        reasons,
        sort=True,
    )
    validation = _validation_requirements(value["validation_requirements"], reasons)
    expected_output = _string_tuple(
        value["expected_output"],
        "INVALID_EXPECTED_OUTPUT",
        reasons,
        sort=True,
    )
    privacy_boundary = _enum(
        value["privacy_boundary"],
        PRIVACY_BOUNDARIES,
        "INVALID_PRIVACY_BOUNDARY",
        reasons,
    )
    dependencies = _string_tuple(
        value["dependencies"],
        "INVALID_DEPENDENCIES",
        reasons,
        pattern=_TOKEN_RE,
        sort=True,
        allow_empty=True,
    )
    expectations = _evidence_expectations(value["evidence_expectations"], reasons)

    if allowed_files == ():
        reasons.append("EMPTY_DECLARED_ALLOWED_FILES")
    if "repository_write" in requested_capabilities and not allowed_files:
        reasons.append("WRITE_WITHOUT_DECLARED_SCOPE")
    if validation is not None and validation.commands == ():
        reasons.append("MISSING_VALIDATION_COMMANDS")
    if expectations is not None and validation is not None:
        if validation.requires_architecture_evidence and not expectations.architecture:
            reasons.append("CONTRADICTORY_ARCHITECTURE_EXPECTATION")
        if validation.requires_real_production_contract and not expectations.production_contract:
            reasons.append("CONTRADICTORY_PRODUCTION_CONTRACT_EXPECTATION")

    protected_files = tuple(path for path in allowed_files if _is_protected_policy_path(path))
    if protected_files and protected_intent != "protected-policy-change-required":
        reasons.append("PROTECTED_POLICY_CHANGE_REQUIRES_INTENT")
    if protected_intent == "protected-policy-change-required" and not protected_files:
        reasons.append("PROTECTED_POLICY_INTENT_WITHOUT_POLICY_SCOPE")

    if reasons:
        return None
    assert isinstance(schema, str)
    assert isinstance(repository, str)
    assert isinstance(base, BaseBinding)
    assert isinstance(branch, str)
    assert isinstance(task_kind, str)
    assert isinstance(risk, str)
    assert isinstance(protected_intent, str)
    assert isinstance(validation, ValidationRequirements)
    assert isinstance(privacy_boundary, str)
    assert isinstance(expectations, EvidenceExpectations)
    return TaskSpec(
        schema=schema,
        repository=repository,
        base=base,
        branch=branch,
        task_kind=task_kind,
        risk=risk,
        protected_intent=protected_intent,
        requested_capabilities=requested_capabilities,
        allowed_files=allowed_files,
        forbidden_actions=forbidden_actions,
        validation_requirements=validation,
        expected_output=expected_output,
        privacy_boundary=privacy_boundary,
        dependencies=dependencies,
        evidence_expectations=expectations,
    )


def _base(value: object, reasons: list[str]) -> BaseBinding | None:
    if not isinstance(value, Mapping):
        reasons.append("INVALID_BASE")
        return None
    ref = _regex(value.get("ref"), _BRANCH_RE, "INVALID_BASE_REF", reasons)
    sha = _regex(value.get("sha"), _SHA_RE, "INVALID_BASE_SHA", reasons)
    if ref is None or sha is None:
        return None
    return BaseBinding(ref=ref, sha=sha.lower())


def _validation_requirements(
    value: object,
    reasons: list[str],
) -> ValidationRequirements | None:
    if not isinstance(value, Mapping):
        reasons.append("INVALID_VALIDATION_REQUIREMENTS")
        return None
    commands = _commands(value.get("commands"), reasons)
    requires_architecture = _bool(
        value.get("requires_architecture_evidence"),
        "INVALID_REQUIRES_ARCHITECTURE_EVIDENCE",
        reasons,
    )
    requires_contract = _bool(
        value.get("requires_real_production_contract"),
        "INVALID_REQUIRES_REAL_PRODUCTION_CONTRACT",
        reasons,
    )
    if commands is None or requires_architecture is None or requires_contract is None:
        return None
    return ValidationRequirements(
        commands=commands,
        requires_architecture_evidence=requires_architecture,
        requires_real_production_contract=requires_contract,
    )


def _evidence_expectations(value: object, reasons: list[str]) -> EvidenceExpectations | None:
    if not isinstance(value, Mapping):
        reasons.append("INVALID_EVIDENCE_EXPECTATIONS")
        return None
    tests = _bool(value.get("tests"), "INVALID_TEST_EVIDENCE_EXPECTATION", reasons)
    architecture = _bool(
        value.get("architecture"),
        "INVALID_ARCHITECTURE_EVIDENCE_EXPECTATION",
        reasons,
    )
    production = _bool(
        value.get("production_contract"),
        "INVALID_PRODUCTION_CONTRACT_EVIDENCE_EXPECTATION",
        reasons,
    )
    runtime = _bool(value.get("runtime"), "INVALID_RUNTIME_EVIDENCE_EXPECTATION", reasons)
    if None in (tests, architecture, production, runtime):
        return None
    return EvidenceExpectations(
        tests=bool(tests),
        architecture=bool(architecture),
        production_contract=bool(production),
        runtime=bool(runtime),
    )


def _commands(value: object, reasons: list[str]) -> tuple[tuple[str, ...], ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        reasons.append("INVALID_VALIDATION_COMMANDS")
        return None
    commands: list[tuple[str, ...]] = []
    for command in value:
        if not isinstance(command, Sequence) or isinstance(command, (str, bytes)):
            reasons.append("INVALID_VALIDATION_COMMANDS")
            return None
        parts = tuple(_bounded_string(part) for part in command)
        if not parts or any(part is None for part in parts):
            reasons.append("INVALID_VALIDATION_COMMANDS")
            return None
        commands.append(tuple(part for part in parts if part is not None))
    return tuple(commands)


def _string_tuple(
    value: object,
    code: str,
    reasons: list[str],
    *,
    allowed: frozenset[str] | None = None,
    pattern: re.Pattern[str] | None = None,
    sort: bool = False,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        reasons.append(code)
        return ()
    result: list[str] = []
    for item in value:
        text = _bounded_string(item)
        if text is None:
            reasons.append(code)
            return ()
        if allowed is not None and text not in allowed:
            reasons.append(code)
            return ()
        if pattern is not None and not pattern.fullmatch(text):
            reasons.append(code)
            return ()
        result.append(text)
    if not allow_empty and not result:
        reasons.append(code)
        return ()
    normalized = tuple(sorted(dict.fromkeys(result)) if sort else dict.fromkeys(result))
    if len(normalized) != len(result):
        reasons.append(f"DUPLICATE_{code.removeprefix('INVALID_')}")
    return normalized


def _exact(value: object, expected: str, code: str, reasons: list[str]) -> str | None:
    if value != expected:
        reasons.append(code)
        return None
    return expected


def _enum(
    value: object,
    allowed: frozenset[str],
    code: str,
    reasons: list[str],
) -> str | None:
    if not isinstance(value, str) or value not in allowed:
        reasons.append(code)
        return None
    return value


def _regex(
    value: object,
    pattern: re.Pattern[str],
    code: str,
    reasons: list[str],
) -> str | None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        reasons.append(code)
        return None
    return value


def _bool(value: object, code: str, reasons: list[str]) -> bool | None:
    if not isinstance(value, bool):
        reasons.append(code)
        return None
    return value


def _bounded_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 512:
        return None
    return text


def _is_protected_policy_path(path: str) -> bool:
    return path in _PROTECTED_POLICY_PATHS or path.startswith(_PROTECTED_PREFIXES)


def public_receipt(validation: TaskSpecValidation) -> Mapping[str, object]:
    profile = validation.predicted_profile
    return MappingProxyType(
        {
            "status": validation.status.value,
            "reason_codes": validation.reason_codes,
            "declared_allowed_file_count": (
                len(profile.declared_scope.allowed_files) if profile else 0
            ),
            "requested_capability_count": (
                len(profile.declared_scope.requested_capabilities) if profile else 0
            ),
            "predicted_risk": profile.risk.value if profile else None,
            "protected_intent": profile.protected_intent if profile else None,
        }
    )
