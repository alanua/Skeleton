from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml


AUTO_MERGE = "AUTO_MERGE"
ASK_OPERATOR = "ASK_OPERATOR"
NEVER_AUTO = "NEVER_AUTO"
BLOCKED = "BLOCKED"
DECISIONS = frozenset({AUTO_MERGE, ASK_OPERATOR, NEVER_AUTO, BLOCKED})

AUTO_MERGE_ALLOWED = "AUTO_MERGE_ALLOWED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
OPERATOR_APPROVAL_REQUIRED = "OPERATOR_APPROVAL_REQUIRED"
DELEGATED_DECISIONS = frozenset(
    {AUTO_MERGE_ALLOWED, REVIEW_REQUIRED, OPERATOR_APPROVAL_REQUIRED, NEVER_AUTO}
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "policies" / "MERGE_DECISION_POLICY.yaml"
DEFAULT_DELEGATED_POLICY_PATH = ROOT / "policies" / "DELEGATED_MERGE_POLICY.yaml"

MERGE_APPROVAL_ACTION = "merge_pull_request"
OPERATOR_MERGE_APPROVAL_MISSING = "operator_merge_approval_missing"
OPERATOR_MERGE_APPROVAL_MALFORMED = "operator_merge_approval_malformed"
OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH = "operator_merge_approval_scope_mismatch"
OPERATOR_MERGE_APPROVAL_HEAD_MISMATCH = "operator_merge_approval_head_mismatch"
TASK_EXPLICITLY_FORBIDS_MERGE = "task_explicitly_forbids_merge"
OPERATOR_MERGE_APPROVAL_VALID = "operator_merge_approval_valid"

MERGE_APPROVAL_REASON_TOKENS = frozenset(
    {
        OPERATOR_MERGE_APPROVAL_MISSING,
        OPERATOR_MERGE_APPROVAL_MALFORMED,
        OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH,
        OPERATOR_MERGE_APPROVAL_HEAD_MISMATCH,
        TASK_EXPLICITLY_FORBIDS_MERGE,
        OPERATOR_MERGE_APPROVAL_VALID,
    }
)

_HEAD_SHA_LENGTHS = (40, 64)
_EXPLICIT_MERGE_PROHIBITIONS = (
    "do not merge",
    "no merge",
    "draft pr only",
    "draft pull request only",
    "must not merge",
    "may not merge",
)


@dataclass(frozen=True)
class MergePolicyResult:
    decision: str
    reasons: tuple[str, ...]
    hard_stop_files_found: tuple[str, ...] = ()
    ask_triggers_found: tuple[str, ...] = ()
    public_metadata: Mapping[str, str | int] | None = None

    @property
    def matched_files(self) -> tuple[str, ...]:
        return self.hard_stop_files_found

    @property
    def matched_triggers(self) -> tuple[str, ...]:
        return self.ask_triggers_found


@dataclass(frozen=True)
class MergePolicyRequest:
    changed_files: tuple[str, ...]
    clean_pr: bool
    evidence_present: bool
    execution_mode_changed: bool = False
    risk_level: str = "green"
    triggers: tuple[str, ...] = ()
    repository: str | None = None
    pr_number: int | None = None
    expected_head_sha: str | None = None
    merge_method: str | None = None
    operator_merge_approval: Mapping[str, Any] | None = None
    task_body: str = ""


MergePolicyDecision = MergePolicyResult


@dataclass(frozen=True)
class DelegatedMergePolicyResult:
    verdict: str
    reasons: tuple[str, ...]
    protected_files_found: tuple[str, ...] = ()
    review_triggers_found: tuple[str, ...] = ()


@dataclass(frozen=True)
class DelegatedMergePolicyRequest:
    changed_files: tuple[str, ...]
    clean_pr: bool
    evidence_present: bool
    risk_level: str = "green"
    triggers: tuple[str, ...] = ()


DelegatedMergePolicyDecision = DelegatedMergePolicyResult


@dataclass(frozen=True)
class OperatorMergeApprovalValidation:
    valid: bool
    reason_token: str
    public_metadata: Mapping[str, str | int]


class MergePolicyChecker:
    def __init__(self, policy_path: Path | str = DEFAULT_POLICY_PATH) -> None:
        self.policy_path = Path(policy_path)
        self.policy = load_merge_decision_policy(self.policy_path)

    def check(self, pr_data: Mapping[str, Any]) -> MergePolicyResult:
        changed_files = _string_tuple(pr_data.get("changed_files"))
        triggers = _string_tuple(pr_data.get("triggers"))
        risk_level = str(pr_data.get("risk_level", "green")).strip().lower()

        hard_stop_files = _matched_file_patterns(
            changed_files,
            _policy_string_list(
                self.policy.get(
                    "hard_stop_file_patterns",
                    self.policy.get("protected_file_patterns"),
                )
            ),
        )
        ask_triggers = _matched_triggers(
            risk_level,
            triggers,
            _policy_string_list(self.policy.get("ask_operator_triggers")),
        )
        level_red_triggers = _matched_level_red_triggers(
            risk_level,
            triggers,
            _policy_string_list(
                self.policy.get("level_red_triggers", self.policy.get("red_level_triggers"))
            ),
        )

        evidence_status = _evidence_status(pr_data)
        if not evidence_status[0]:
            return MergePolicyResult(
                decision=BLOCKED,
                reasons=(evidence_status[1],),
                hard_stop_files_found=hard_stop_files,
                ask_triggers_found=ask_triggers,
            )

        if level_red_triggers:
            return MergePolicyResult(
                decision=NEVER_AUTO,
                reasons=(
                    "level_red trigger found: " + ", ".join(level_red_triggers),
                ),
                hard_stop_files_found=hard_stop_files,
                ask_triggers_found=ask_triggers,
            )

        if hard_stop_files:
            return MergePolicyResult(
                decision=ASK_OPERATOR,
                reasons=("hard-stop file changed: " + ", ".join(hard_stop_files),),
                hard_stop_files_found=hard_stop_files,
                ask_triggers_found=ask_triggers,
            )

        if ask_triggers:
            return MergePolicyResult(
                decision=ASK_OPERATOR,
                reasons=("operator review trigger found: " + ", ".join(ask_triggers),),
                hard_stop_files_found=hard_stop_files,
                ask_triggers_found=ask_triggers,
            )

        if not _bool_value(pr_data.get("clean_pr")):
            return MergePolicyResult(
                decision=ASK_OPERATOR,
                reasons=("clean PR condition is not satisfied.",),
                hard_stop_files_found=hard_stop_files,
                ask_triggers_found=ask_triggers,
            )

        approval_result = validate_operator_merge_approval(
            pr_data.get("operator_merge_approval"),
            repository=pr_data.get("repository"),
            pr_number=pr_data.get("pr_number"),
            expected_head_sha=pr_data.get("expected_head_sha"),
            merge_method=pr_data.get("merge_method"),
            task_body=str(pr_data.get("task_body") or pr_data.get("body") or ""),
        )
        if not approval_result.valid:
            return MergePolicyResult(
                decision=(
                    BLOCKED
                    if approval_result.reason_token == TASK_EXPLICITLY_FORBIDS_MERGE
                    else ASK_OPERATOR
                ),
                reasons=(approval_result.reason_token,),
                hard_stop_files_found=hard_stop_files,
                ask_triggers_found=ask_triggers,
                public_metadata=approval_result.public_metadata,
            )

        return MergePolicyResult(
            decision=AUTO_MERGE,
            reasons=(OPERATOR_MERGE_APPROVAL_VALID,),
            public_metadata=approval_result.public_metadata,
        )


class DelegatedMergePolicyChecker:
    """Review-only delegated merge policy checker.

    This checker is additive to MergePolicyChecker. It does not perform network,
    subprocess, filesystem write, or merge operations; it only evaluates supplied
    PR metadata against a loaded policy mapping.
    """

    def __init__(
        self, policy_path: Path | str = DEFAULT_DELEGATED_POLICY_PATH
    ) -> None:
        self.policy_path = Path(policy_path)
        self.policy = load_delegated_merge_policy(self.policy_path)

    def check(self, pr_data: Mapping[str, Any]) -> DelegatedMergePolicyResult:
        changed_files = _string_tuple(pr_data.get("changed_files"))
        triggers = _string_tuple(pr_data.get("triggers"))
        risk_level = str(pr_data.get("risk_level", "green")).strip().lower()

        protected_files = _matched_file_patterns(
            changed_files,
            _policy_string_list(self.policy.get("operator_approval_file_patterns")),
        )
        operator_triggers = _matched_triggers(
            risk_level,
            triggers,
            _policy_string_list(self.policy.get("operator_approval_triggers")),
        )
        never_auto_triggers = _matched_level_red_triggers(
            risk_level,
            triggers,
            _policy_string_list(self.policy.get("never_auto_triggers")),
        )
        review_triggers = _matched_triggers(
            risk_level,
            triggers,
            _policy_string_list(self.policy.get("review_required_triggers")),
        )

        evidence_status = _evidence_status(pr_data)
        if not evidence_status[0]:
            return DelegatedMergePolicyResult(
                verdict=REVIEW_REQUIRED,
                reasons=(evidence_status[1],),
                protected_files_found=protected_files,
                review_triggers_found=review_triggers,
            )

        if never_auto_triggers:
            return DelegatedMergePolicyResult(
                verdict=NEVER_AUTO,
                reasons=(
                    "never-auto trigger found: " + ", ".join(never_auto_triggers),
                ),
                protected_files_found=protected_files,
                review_triggers_found=review_triggers,
            )

        if protected_files:
            return DelegatedMergePolicyResult(
                verdict=OPERATOR_APPROVAL_REQUIRED,
                reasons=(
                    "operator approval file changed: " + ", ".join(protected_files),
                ),
                protected_files_found=protected_files,
                review_triggers_found=review_triggers,
            )

        if operator_triggers:
            return DelegatedMergePolicyResult(
                verdict=OPERATOR_APPROVAL_REQUIRED,
                reasons=(
                    "operator approval trigger found: "
                    + ", ".join(operator_triggers),
                ),
                protected_files_found=protected_files,
                review_triggers_found=review_triggers,
            )

        if review_triggers:
            return DelegatedMergePolicyResult(
                verdict=REVIEW_REQUIRED,
                reasons=("review trigger found: " + ", ".join(review_triggers),),
                protected_files_found=protected_files,
                review_triggers_found=review_triggers,
            )

        if not _bool_value(pr_data.get("clean_pr")):
            return DelegatedMergePolicyResult(
                verdict=REVIEW_REQUIRED,
                reasons=("clean PR condition is not satisfied.",),
                protected_files_found=protected_files,
                review_triggers_found=review_triggers,
            )

        return DelegatedMergePolicyResult(verdict=AUTO_MERGE_ALLOWED, reasons=())


def load_merge_decision_policy(
    policy_path: Path | str = DEFAULT_POLICY_PATH,
) -> dict[str, Any]:
    """Load the stage 1 merge decision policy from YAML."""
    policy = yaml.safe_load(Path(policy_path).read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("merge decision policy must be a mapping")

    decisions = policy.get("decisions")
    if not isinstance(decisions, dict) or set(decisions) != DECISIONS:
        raise ValueError("merge decision policy must define all decision states")

    required = policy.get("auto_merge_requires")
    if not isinstance(required, list) or not required:
        raise ValueError("merge decision policy must define auto_merge_requires")

    _policy_string_list(
        policy.get("hard_stop_file_patterns", policy.get("protected_file_patterns"))
    )
    _policy_string_list(policy.get("ask_operator_triggers"))
    _policy_string_list(policy.get("level_red_triggers"))
    return policy


def load_delegated_merge_policy(
    policy_path: Path | str = DEFAULT_DELEGATED_POLICY_PATH,
) -> dict[str, Any]:
    """Load the additive delegated merge review policy from YAML."""
    policy = yaml.safe_load(Path(policy_path).read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("delegated merge policy must be a mapping")

    decisions = policy.get("verdicts")
    if not isinstance(decisions, dict) or set(decisions) != DELEGATED_DECISIONS:
        raise ValueError("delegated merge policy must define all verdict states")

    required = policy.get("auto_merge_allowed_requires")
    if not isinstance(required, list) or not required:
        raise ValueError("delegated merge policy must define auto_merge_allowed_requires")

    _policy_string_list(policy.get("operator_approval_file_patterns"))
    _policy_string_list(policy.get("operator_approval_triggers"))
    _policy_string_list(policy.get("review_required_triggers"))
    _policy_string_list(policy.get("never_auto_triggers"))
    return policy


def check_merge_policy(
    request: MergePolicyRequest,
    policy: Optional[Mapping[str, Any]] = None,
) -> MergePolicyDecision:
    """Compatibility wrapper around MergePolicyChecker.check()."""
    checker = MergePolicyChecker.__new__(MergePolicyChecker)
    checker.policy_path = DEFAULT_POLICY_PATH
    checker.policy = dict(policy) if policy is not None else load_merge_decision_policy()
    triggers = list(request.triggers)
    if request.execution_mode_changed:
        triggers.append("execution_mode_changed")

    return checker.check(
        {
            "changed_files": request.changed_files,
            "clean_pr": request.clean_pr,
            "evidence": {"present": request.evidence_present},
            "risk_level": request.risk_level,
            "triggers": tuple(triggers),
            "repository": request.repository,
            "pr_number": request.pr_number,
            "expected_head_sha": request.expected_head_sha,
            "merge_method": request.merge_method,
            "operator_merge_approval": request.operator_merge_approval,
            "task_body": request.task_body,
        }
    )


def validate_operator_merge_approval(
    approval: object,
    *,
    repository: object,
    pr_number: object,
    expected_head_sha: object,
    merge_method: object,
    task_body: str = "",
) -> OperatorMergeApprovalValidation:
    """Validate an action-specific, PR-state-bound merge approval object.

    The returned metadata is intentionally bounded and public-safe. It never
    echoes the approval payload or raw task/comment text.
    """
    metadata = _merge_approval_public_metadata(
        repository=repository,
        pr_number=pr_number,
        expected_head_sha=expected_head_sha,
        merge_method=merge_method,
    )
    prohibition = _explicit_merge_prohibition(task_body)

    if approval is None:
        reason = (
            TASK_EXPLICITLY_FORBIDS_MERGE
            if prohibition is not None
            else OPERATOR_MERGE_APPROVAL_MISSING
        )
        return OperatorMergeApprovalValidation(False, reason, metadata)

    if not isinstance(approval, Mapping):
        if prohibition is not None:
            return OperatorMergeApprovalValidation(
                False, TASK_EXPLICITLY_FORBIDS_MERGE, metadata
            )
        return OperatorMergeApprovalValidation(
            False, OPERATOR_MERGE_APPROVAL_MALFORMED, metadata
        )

    if prohibition is not None and approval.get("supersedes_task_prohibition") != prohibition:
        return OperatorMergeApprovalValidation(
            False, TASK_EXPLICITLY_FORBIDS_MERGE, metadata
        )

    required_fields = (
        "action",
        "repository",
        "pr_number",
        "expected_head_sha",
        "merge_method",
    )
    if any(field not in approval for field in required_fields):
        return OperatorMergeApprovalValidation(
            False, OPERATOR_MERGE_APPROVAL_MALFORMED, metadata
        )

    approved_action = approval.get("action")
    approved_repo = approval.get("repository")
    approved_pr = approval.get("pr_number")
    approved_head = approval.get("expected_head_sha")
    approved_method = approval.get("merge_method")
    if (
        not isinstance(approved_action, str)
        or not isinstance(approved_repo, str)
        or not isinstance(approved_method, str)
        or not isinstance(approved_head, str)
        or not isinstance(approved_pr, int)
        or isinstance(approved_pr, bool)
        or not _valid_repo(repository)
        or not isinstance(pr_number, int)
        or isinstance(pr_number, bool)
        or not _valid_head_sha(expected_head_sha)
        or not _valid_head_sha(approved_head)
        or not _valid_merge_method(merge_method)
    ):
        return OperatorMergeApprovalValidation(
            False, OPERATOR_MERGE_APPROVAL_MALFORMED, metadata
        )

    if str(expected_head_sha).lower() != approved_head.lower():
        return OperatorMergeApprovalValidation(
            False, OPERATOR_MERGE_APPROVAL_HEAD_MISMATCH, metadata
        )

    if (
        approved_action != MERGE_APPROVAL_ACTION
        or approved_repo != repository
        or approved_pr != pr_number
        or approved_method != merge_method
    ):
        return OperatorMergeApprovalValidation(
            False, OPERATOR_MERGE_APPROVAL_SCOPE_MISMATCH, metadata
        )

    return OperatorMergeApprovalValidation(
        True, OPERATOR_MERGE_APPROVAL_VALID, metadata
    )


def check_delegated_merge_policy(
    request: DelegatedMergePolicyRequest,
    policy: Optional[Mapping[str, Any]] = None,
) -> DelegatedMergePolicyDecision:
    """Compatibility wrapper around DelegatedMergePolicyChecker.check()."""
    checker = DelegatedMergePolicyChecker.__new__(DelegatedMergePolicyChecker)
    checker.policy_path = DEFAULT_DELEGATED_POLICY_PATH
    checker.policy = (
        dict(policy) if policy is not None else load_delegated_merge_policy()
    )
    return checker.check(
        {
            "changed_files": request.changed_files,
            "clean_pr": request.clean_pr,
            "evidence": {"present": request.evidence_present},
            "risk_level": request.risk_level,
            "triggers": request.triggers,
        }
    )


def _merge_approval_public_metadata(
    *,
    repository: object,
    pr_number: object,
    expected_head_sha: object,
    merge_method: object,
) -> dict[str, str | int]:
    metadata: dict[str, str | int] = {}
    if isinstance(repository, str) and _valid_repo(repository):
        metadata["repository"] = repository
    if isinstance(pr_number, int) and not isinstance(pr_number, bool) and pr_number > 0:
        metadata["pr_number"] = pr_number
    if isinstance(expected_head_sha, str) and _valid_head_sha(expected_head_sha):
        metadata["expected_head_sha"] = expected_head_sha.lower()
    if isinstance(merge_method, str) and _valid_merge_method(merge_method):
        metadata["merge_method"] = merge_method
    return metadata


def _valid_repo(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split("/")
    return (
        len(parts) == 2
        and all(part != "" for part in parts)
        and all(
            all(char.isalnum() or char in ".-_" for char in part)
            for part in parts
        )
    )


def _valid_head_sha(value: object) -> bool:
    if not isinstance(value, str) or len(value) not in _HEAD_SHA_LENGTHS:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def _valid_merge_method(value: object) -> bool:
    return isinstance(value, str) and value in {"merge", "squash", "rebase"}


def _explicit_merge_prohibition(body: str) -> str | None:
    lowered = body.lower()
    for phrase in _EXPLICIT_MERGE_PROHIBITIONS:
        if phrase in lowered:
            return phrase
    return None


def _evidence_status(pr_data: Mapping[str, Any]) -> tuple[bool, str]:
    evidence = pr_data.get("evidence")
    if isinstance(evidence, Mapping):
        if not evidence:
            return False, "missing required merge evidence."
        unverifiable = [
            str(key)
            for key, value in evidence.items()
            if not isinstance(value, bool) or value is not True
        ]
        if unverifiable:
            return False, "unverifiable merge evidence: " + ", ".join(unverifiable)
        return True, ""

    if _bool_value(pr_data.get("evidence_present")) is True:
        return True, ""

    return False, "missing required merge evidence."


def _matched_file_patterns(
    changed_files: tuple[str, ...],
    patterns: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        changed_file
        for changed_file in changed_files
        if any(fnmatchcase(changed_file, pattern) for pattern in patterns)
    )


def _matched_triggers(
    risk_level: str,
    triggers: tuple[str, ...],
    policy_triggers: tuple[str, ...],
) -> tuple[str, ...]:
    matched = []
    policy_trigger_set = set(policy_triggers)
    matched.extend(trigger for trigger in triggers if trigger in policy_trigger_set)
    if risk_level in policy_trigger_set:
        matched.append(risk_level)
    return tuple(matched)


def _matched_level_red_triggers(
    risk_level: str,
    triggers: tuple[str, ...],
    policy_triggers: tuple[str, ...],
) -> tuple[str, ...]:
    matched = []
    if risk_level == "red":
        matched.append("risk_level:red")

    policy_trigger_set = set(policy_triggers)
    matched.extend(trigger for trigger in triggers if trigger in policy_trigger_set)
    return tuple(matched)


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError("PR data list values must be strings")
    return tuple(value)


def _policy_string_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("policy list values must be lists of strings")
    return tuple(value)


def _bool_value(value: object) -> bool:
    return isinstance(value, bool) and value is True
