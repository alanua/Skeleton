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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "policies" / "MERGE_DECISION_POLICY.yaml"


@dataclass(frozen=True)
class MergePolicyResult:
    decision: str
    reasons: tuple[str, ...]
    hard_stop_files_found: tuple[str, ...] = ()
    ask_triggers_found: tuple[str, ...] = ()

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


MergePolicyDecision = MergePolicyResult


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

        return MergePolicyResult(decision=AUTO_MERGE, reasons=())


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
        }
    )


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
