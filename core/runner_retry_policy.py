from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


PLACEHOLDER_EXPECTED_OUTPUTS = frozenset(
    {
        "todo",
        "tbd",
        "n/a",
        "na",
        "none",
        "placeholder",
        "{expected_output}",
        "<expected_output>",
        "expected_output",
    }
)


@dataclass(frozen=True)
class ExpectedOutputValidation:
    accepted: bool
    reason: str | None = None


def expected_output_validation(value: object) -> ExpectedOutputValidation:
    if value is None:
        return ExpectedOutputValidation(False, "missing_expected_output")
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        return ExpectedOutputValidation(False, "invalid_expected_output")

    normalized = [item.strip() for item in items]
    if not normalized or any(not item for item in normalized):
        return ExpectedOutputValidation(False, "empty_expected_output")
    if any(item.lower() in PLACEHOLDER_EXPECTED_OUTPUTS for item in normalized):
        return ExpectedOutputValidation(False, "placeholder_expected_output")
    return ExpectedOutputValidation(True)


def one_time_override_hash(override: dict[str, Any]) -> str:
    encoded = json.dumps(override, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:16]
