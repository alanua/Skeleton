from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Iterable, Mapping


AUTO_MERGE_ALLOWED = "AUTO_MERGE_ALLOWED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
OPERATOR_APPROVAL_REQUIRED = "OPERATOR_APPROVAL_REQUIRED"
NEVER_AUTO = "NEVER_AUTO"

DELEGATED_MERGE_VERDICTS = frozenset(
    {
        AUTO_MERGE_ALLOWED,
        REVIEW_REQUIRED,
        OPERATOR_APPROVAL_REQUIRED,
        NEVER_AUTO,
    }
)

PROTECTED_PATH_PATTERNS = (
    "scripts/runner_poll_github_tasks.py",
    "BOOT_MANIFEST.yaml",
    "PROJECT_TREE.yaml",
    "OPERATOR_RULES.yaml",
    "CAPABILITY_REGISTRY.yaml",
    ".github/workflows/**",
    "policies/**",
    "deploy/**",
    "deploy",
    "runtime/**",
    "runtime",
    "secrets/**",
    "secrets",
    "server/**",
    "server",
    "finance/**",
    "finance",
    "legal/**",
    "legal",
    "governance/**",
    "governance",
    "scripts/*.service",
    "scripts/*.timer",
    "skills/**/approval/**",
    "skills/**/promotion/**",
)


@dataclass(frozen=True)
class DelegatedMergePolicyResult:
    verdict: str
    reasons: tuple[str, ...]
    protected_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class DelegatedMergePolicyInput:
    changed_files: tuple[str, ...]
    validation_passed: bool
    diff_clean: bool
    secrets_detected: bool
    approved_scope: bool
    public_safe: bool
    file_count_limit: int


def evaluate_delegated_merge_policy(
    *,
    changed_files: Iterable[str],
    validation_passed: bool,
    diff_clean: bool,
    secrets_detected: bool,
    approved_scope: bool,
    public_safe: bool,
    file_count_limit: int,
) -> DelegatedMergePolicyResult:
    """Return a delegated merge review verdict from plain input data.

    This function is intentionally pure: it reads no files, writes no files,
    starts no subprocesses, performs no network calls, and does not call any
    GitHub API. The verdict is advisory review output only.
    """
    normalized_files = _normalize_changed_files(changed_files)
    normalized_limit = _normalize_file_count_limit(file_count_limit)
    protected_files = _matched_protected_files(normalized_files)

    never_auto_reasons = []
    if secrets_detected:
        never_auto_reasons.append("secrets detected")
    if not public_safe:
        never_auto_reasons.append("output is not public-safe")
    if never_auto_reasons:
        return DelegatedMergePolicyResult(
            verdict=NEVER_AUTO,
            reasons=tuple(never_auto_reasons),
            protected_files=protected_files,
        )

    if protected_files:
        return DelegatedMergePolicyResult(
            verdict=OPERATOR_APPROVAL_REQUIRED,
            reasons=("protected files changed: " + ", ".join(protected_files),),
            protected_files=protected_files,
        )

    review_reasons = []
    if not approved_scope:
        review_reasons.append("approved scope is required")
    if not validation_passed:
        review_reasons.append("validation must pass")
    if not diff_clean:
        review_reasons.append("diff must be clean")
    if len(normalized_files) > normalized_limit:
        review_reasons.append(
            f"file count exceeds limit: {len(normalized_files)} > {normalized_limit}"
        )

    if review_reasons:
        return DelegatedMergePolicyResult(
            verdict=REVIEW_REQUIRED,
            reasons=tuple(review_reasons),
            protected_files=protected_files,
        )

    return DelegatedMergePolicyResult(
        verdict=AUTO_MERGE_ALLOWED,
        reasons=(),
        protected_files=protected_files,
    )


def check_delegated_merge_policy(
    request: DelegatedMergePolicyInput | Mapping[str, object],
) -> DelegatedMergePolicyResult:
    if isinstance(request, DelegatedMergePolicyInput):
        return evaluate_delegated_merge_policy(
            changed_files=request.changed_files,
            validation_passed=request.validation_passed,
            diff_clean=request.diff_clean,
            secrets_detected=request.secrets_detected,
            approved_scope=request.approved_scope,
            public_safe=request.public_safe,
            file_count_limit=request.file_count_limit,
        )

    return evaluate_delegated_merge_policy(
        changed_files=_required_value(request, "changed_files"),
        validation_passed=_required_bool(request, "validation_passed"),
        diff_clean=_required_bool(request, "diff_clean"),
        secrets_detected=_required_bool(request, "secrets_detected"),
        approved_scope=_required_bool(request, "approved_scope"),
        public_safe=_required_bool(request, "public_safe"),
        file_count_limit=_required_int(request, "file_count_limit"),
    )


def _normalize_changed_files(changed_files: Iterable[str]) -> tuple[str, ...]:
    if isinstance(changed_files, str):
        raise TypeError("changed_files must be an iterable of strings")

    normalized = []
    for changed_file in changed_files:
        if not isinstance(changed_file, str):
            raise TypeError("changed_files must contain only strings")
        path = changed_file.strip().replace("\\", "/")
        if path:
            normalized.append(path)
    return tuple(normalized)


def _normalize_file_count_limit(file_count_limit: int) -> int:
    if not isinstance(file_count_limit, int) or isinstance(file_count_limit, bool):
        raise TypeError("file_count_limit must be an integer")
    if file_count_limit < 0:
        raise ValueError("file_count_limit must be non-negative")
    return file_count_limit


def _matched_protected_files(changed_files: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        changed_file
        for changed_file in changed_files
        if any(fnmatchcase(changed_file, pattern) for pattern in PROTECTED_PATH_PATTERNS)
    )


def _required_value(request: Mapping[str, object], key: str) -> object:
    if key not in request:
        raise KeyError(f"missing delegated merge policy input: {key}")
    return request[key]


def _required_bool(request: Mapping[str, object], key: str) -> bool:
    value = _required_value(request, key)
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _required_int(request: Mapping[str, object], key: str) -> int:
    value = _required_value(request, key)
    return _normalize_file_count_limit(value)  # type: ignore[arg-type]
