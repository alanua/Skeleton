from __future__ import annotations

from typing import Any, Mapping

from core.task_envelope import TaskEnvelope

RISK_RANK = {"green": 0, "yellow": 1, "red": 2}
READ_ONLY_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
READ_ONLY_FILESYSTEM_OPERATIONS = frozenset({"read_text"})
READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"diff", "log", "ls-files", "ls-tree", "rev-parse", "show", "status"}
)


class TaskRiskPolicyError(ValueError):
    pass


def enforce_task_risk(envelope: TaskEnvelope) -> None:
    required = minimum_risk(envelope.executor_class, envelope.steps)
    if RISK_RANK[envelope.risk_class] < RISK_RANK[required]:
        raise TaskRiskPolicyError(
            f"executor operation requires risk_class {required} or higher"
        )


def minimum_risk(
    executor_class: str,
    steps: tuple[Mapping[str, Any], ...],
) -> str:
    if executor_class == "network.http":
        methods = {
            str(step.get("method", "GET")).upper()
            for step in steps or ({},)
        }
        return "green" if methods <= READ_ONLY_HTTP_METHODS else "yellow"

    if executor_class == "filesystem":
        operations = {str(step.get("operation", "")) for step in steps}
        if operations and operations <= READ_ONLY_FILESYSTEM_OPERATIONS:
            return "green"
        return "yellow"

    if executor_class == "repository":
        for step in steps:
            argv = step.get("argv")
            if (
                not isinstance(argv, list)
                or len(argv) < 2
                or argv[0] != "git"
                or argv[1] not in READ_ONLY_GIT_SUBCOMMANDS
            ):
                return "yellow"
        return "green" if steps else "yellow"

    if executor_class == "composite":
        required = "green"
        for step in steps:
            nested_class = step.get("executor_class")
            if not isinstance(nested_class, str) or nested_class == "composite":
                return "yellow"
            nested = minimum_risk(nested_class, (step,))
            if RISK_RANK[nested] > RISK_RANK[required]:
                required = nested
        return required

    return "yellow"
