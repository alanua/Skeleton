from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Final


_SHA_RE: Final = re.compile(r"^[0-9a-fA-F]{40}$")


class EvidenceLevel(str, Enum):
    DECLARED_ONLY = "DECLARED_ONLY"
    HEAD_BOUND_VALIDATION = "HEAD_BOUND_VALIDATION"
    ARCHITECTURE_REVIEW_REQUIRED = "ARCHITECTURE_REVIEW_REQUIRED"
    ARCHITECTURE_GREEN = "ARCHITECTURE_GREEN"
    RUNTIME_PROVEN = "RUNTIME_PROVEN"


class EvidenceValidationError(ValueError):
    """Raised when quality evidence is malformed or not applicable."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class HeadBoundEvidence:
    repo: str
    base_sha: str
    head_sha: str
    validation_commands: tuple[tuple[str, ...], ...]
    tests_passed: bool
    evidence_level: EvidenceLevel = EvidenceLevel.HEAD_BOUND_VALIDATION

    @classmethod
    def from_mapping(cls, value: object) -> HeadBoundEvidence:
        if not isinstance(value, dict):
            raise EvidenceValidationError(
                "INVALID_EVIDENCE_PACKET",
                "evidence packet must be an object",
            )
        repo = _required_string(value, "repo")
        base_sha = _sha(_required_string(value, "base_sha"), "base_sha")
        head_sha = _sha(_required_string(value, "head_sha"), "head_sha")
        commands = _commands(value.get("validation_commands", ()))
        tests_passed = value.get("tests_passed")
        if not isinstance(tests_passed, bool):
            raise EvidenceValidationError(
                "INVALID_TEST_EVIDENCE",
                "tests_passed must be a boolean evidence field",
            )
        level = value.get("evidence_level", EvidenceLevel.HEAD_BOUND_VALIDATION.value)
        if level in {
            EvidenceLevel.RUNTIME_PROVEN.value,
            EvidenceLevel.ARCHITECTURE_GREEN.value,
        }:
            raise EvidenceValidationError(
                "UNREACHABLE_PHASE1_EVIDENCE",
                "Phase 1 cannot accept runtime or architecture-green evidence",
            )
        if level != EvidenceLevel.HEAD_BOUND_VALIDATION.value:
            raise EvidenceValidationError(
                "INVALID_EVIDENCE_LEVEL",
                "Phase 1 evidence must be head-bound validation evidence",
            )
        return cls(
            repo=repo,
            base_sha=base_sha,
            head_sha=head_sha,
            validation_commands=commands,
            tests_passed=tests_passed,
        )

    def is_valid_for_head(self, *, repo: str, base_sha: str, head_sha: str) -> bool:
        return (
            self.repo == repo
            and self.base_sha == base_sha.lower()
            and self.head_sha == head_sha.lower()
        )


def _required_string(value: dict[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a non-empty string",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in item):
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must not contain control characters",
        )
    return item


def _sha(value: str, field: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise EvidenceValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a 40-character commit SHA",
        )
    return value.lower()


def _commands(value: object) -> tuple[tuple[str, ...], ...]:
    if value in (None, ()):
        return ()
    if not isinstance(value, list):
        raise EvidenceValidationError(
            "INVALID_VALIDATION_COMMANDS",
            "validation_commands must be an array of argv arrays",
        )
    normalized: list[tuple[str, ...]] = []
    for command in value:
        if not isinstance(command, list) or not command:
            raise EvidenceValidationError(
                "INVALID_VALIDATION_COMMAND",
                "validation command must be a non-empty argv array",
            )
        argv: list[str] = []
        for argument in command:
            if (
                not isinstance(argument, str)
                or not argument
                or any(ord(character) < 32 or ord(character) == 127 for character in argument)
            ):
                raise EvidenceValidationError(
                    "INVALID_VALIDATION_COMMAND",
                    "validation command arguments must be bounded text",
                )
            argv.append(argument)
        normalized.append(tuple(argv))
    return tuple(normalized)
