from __future__ import annotations

from typing import Final

FAILURE_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "EXECUTOR_UNAVAILABLE",
        "EXECUTOR_AUTH_UNAVAILABLE",
        "MODEL_QUOTA",
        "MODEL_PROVIDER_OUTAGE",
        "MODEL_UNSUPPORTED",
        "TOOLCHAIN_CAPABILITY_MISMATCH",
        "NO_PROGRESS",
        "DELIVERABLE_MISSING",
        "VALIDATION_FAILED",
        "TRANSIENT_IO",
        "POLICY_DENIED",
        "BUDGET_EXHAUSTED",
        "PRIVACY_DENIED",
        "AMBIGUOUS_SIDE_EFFECT",
    }
)


def require_failure_class(value: str) -> str:
    if value not in FAILURE_CLASSES:
        raise ValueError("unknown_failure_class")
    return value
