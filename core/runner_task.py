from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import re
from types import MappingProxyType
from typing import Any, Final


RUNNER_TASK_SCHEMA: Final = "skeleton.runner_task.v1"
TASK_KINDS: Final = frozenset(
    {
        "code_edit",
        "repository_maintenance",
        "private_memory",
        "diagnostic",
        "loop_control",
        "publish",
    }
)
REQUESTED_CAPABILITIES: Final = frozenset(
    {
        "repository_read",
        "repository_write_allowlisted",
        "test_execution",
        "subprocess_isolated",
        "memory_gateway_read",
        "memory_gateway_write",
        "diagnostic_read",
        "repository_maintenance",
        "loop_control",
        "publish_pull_request",
    }
)
PRIVACY_BOUNDARIES: Final = frozenset(
    {
        "PUBLIC_SAFE_REPOSITORY_ONLY",
        "PUBLIC_SAFE_AGGREGATE_ONLY",
        "LOCAL_PRIVATE",
        "PRIVATE_LOCAL",
    }
)
MIN_VALIDATION_TIMEOUT_SECONDS: Final = 1
MAX_VALIDATION_TIMEOUT_SECONDS: Final = 3600

_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PAYLOAD_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,511}$")

_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "repo",
        "branch",
        "base_sha",
        "task_kind",
        "payload",
        "requested_capabilities",
        "allowed_files",
        "forbidden_actions",
        "validation_commands",
        "validation_timeout_seconds",
        "expected_output",
        "privacy_boundary",
        "approval_reference",
        "idempotency_key",
    }
)

_MAX_PAYLOAD_DEPTH = 16
_MAX_PAYLOAD_ITEMS = 256
_MAX_PAYLOAD_STRING_LENGTH = 4096
_MAX_PAYLOAD_BYTES = 65536
_MAX_COMMANDS = 16
_MAX_COMMAND_ARGUMENTS = 64
_MAX_ARGUMENT_LENGTH = 2048
_MAX_TEXT_ITEMS = 64
_MAX_TEXT_LENGTH = 512


class RunnerTaskValidationError(ValueError):
    """Raised when a Runner task envelope is malformed or out of policy."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RunnerTask:
    schema: str
    repo: str
    branch: str
    base_sha: str
    task_kind: str
    payload: Mapping[str, Any]
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    validation_commands: tuple[tuple[str, ...], ...]
    validation_timeout_seconds: int
    expected_output: tuple[str, ...]
    privacy_boundary: str
    approval_reference: str
    idempotency_key: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RunnerTask:
        if not isinstance(value, Mapping):
            raise RunnerTaskValidationError(
                "INVALID_TASK_ENVELOPE",
                "Runner task envelope must be an object",
            )
        if any(not isinstance(key, str) for key in value):
            raise RunnerTaskValidationError(
                "INVALID_TASK_FIELD",
                "Runner task envelope keys must be strings",
            )

        keys = frozenset(value)
        unknown = sorted(keys - _REQUIRED_FIELDS)
        if unknown:
            raise RunnerTaskValidationError(
                "UNKNOWN_TASK_FIELD",
                f"unknown Runner task field: {unknown[0]}",
            )
        missing = sorted(_REQUIRED_FIELDS - keys)
        if missing:
            raise RunnerTaskValidationError(
                "MISSING_TASK_FIELD",
                f"missing Runner task field: {missing[0]}",
            )

        schema = _exact_string(value["schema"], "schema", RUNNER_TASK_SCHEMA)
        repo = _repository(value["repo"])
        branch = _branch(value["branch"])
        base_sha = _base_sha(value["base_sha"])
        task_kind = _enum_string(value["task_kind"], "task_kind", TASK_KINDS)
        payload = _payload(value["payload"])
        requested_capabilities = _capabilities(value["requested_capabilities"])
        allowed_files = _allowed_files(value["allowed_files"])
        forbidden_actions = _bounded_text_items(
            value["forbidden_actions"],
            field="forbidden_actions",
            sort_items=True,
        )
        validation_commands = _validation_commands(value["validation_commands"])
        validation_timeout_seconds = _validation_timeout(
            value["validation_timeout_seconds"]
        )
        expected_output = _bounded_text_items(
            value["expected_output"],
            field="expected_output",
            sort_items=True,
        )
        privacy_boundary = _enum_string(
            value["privacy_boundary"],
            "privacy_boundary",
            PRIVACY_BOUNDARIES,
        )
        approval_reference = _safe_token(
            value["approval_reference"], "approval_reference"
        )
        idempotency_key = _safe_token(value["idempotency_key"], "idempotency_key")

        return cls(
            schema=schema,
            repo=repo,
            branch=branch,
            base_sha=base_sha,
            task_kind=task_kind,
            payload=payload,
            requested_capabilities=requested_capabilities,
            allowed_files=allowed_files,
            forbidden_actions=forbidden_actions,
            validation_commands=validation_commands,
            validation_timeout_seconds=validation_timeout_seconds,
            expected_output=expected_output,
            privacy_boundary=privacy_boundary,
            approval_reference=approval_reference,
            idempotency_key=idempotency_key,
        )

    @classmethod
    def from_json(cls, value: str) -> RunnerTask:
        if not isinstance(value, str):
            raise RunnerTaskValidationError(
                "INVALID_TASK_JSON",
                "Runner task JSON must be a string",
            )
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RunnerTaskValidationError(
                "INVALID_TASK_JSON",
                "Runner task JSON is malformed",
            ) from exc
        return cls.from_mapping(parsed)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "repo": self.repo,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "task_kind": self.task_kind,
            "payload": _thaw_json(self.payload),
            "requested_capabilities": list(self.requested_capabilities),
            "allowed_files": list(self.allowed_files),
            "forbidden_actions": list(self.forbidden_actions),
            "validation_commands": [list(command) for command in self.validation_commands],
            "validation_timeout_seconds": self.validation_timeout_seconds,
            "expected_output": list(self.expected_output),
            "privacy_boundary": self.privacy_boundary,
            "approval_reference": self.approval_reference,
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


def _exact_string(value: object, field: str, expected: str) -> str:
    if value != expected:
        raise RunnerTaskValidationError(
            "INVALID_TASK_SCHEMA",
            f"{field} must equal {expected}",
        )
    return expected


def _repository(value: object) -> str:
    if not isinstance(value, str) or not _REPOSITORY_RE.fullmatch(value):
        raise RunnerTaskValidationError(
            "INVALID_REPOSITORY",
            "repo must be an owner/name repository identifier",
        )
    return value


def _branch(value: object) -> str:
    if not isinstance(value, str) or not _BRANCH_RE.fullmatch(value):
        raise RunnerTaskValidationError(
            "INVALID_BRANCH",
            "branch is malformed",
        )
    if (
        value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(segment in {"", ".", ".."} for segment in value.split("/"))
    ):
        raise RunnerTaskValidationError(
            "INVALID_BRANCH",
            "branch is malformed",
        )
    return value


def _base_sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise RunnerTaskValidationError(
            "INVALID_BASE_SHA",
            "base_sha must be a full 40-character commit SHA",
        )
    return value.lower()


def _enum_string(
    value: object,
    field: str,
    allowed: frozenset[str],
) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise RunnerTaskValidationError(
            f"INVALID_{field.upper()}",
            f"{field} is not allowlisted",
        )
    return value


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise RunnerTaskValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a bounded token",
        )
    return value


def _payload(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RunnerTaskValidationError(
            "INVALID_ROUTE_PAYLOAD",
            "payload must be an object",
        )
    frozen = _freeze_json(value, path="payload", depth=0)
    assert isinstance(frozen, Mapping)
    serialized = json.dumps(
        _thaw_json(frozen),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(serialized.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise RunnerTaskValidationError(
            "ROUTE_PAYLOAD_TOO_LARGE",
            "payload exceeds the bounded JSON size",
        )
    return frozen


def _freeze_json(value: object, *, path: str, depth: int) -> Any:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise RunnerTaskValidationError(
            "ROUTE_PAYLOAD_TOO_DEEP",
            f"{path} exceeds the maximum JSON depth",
        )
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RunnerTaskValidationError(
                "INVALID_ROUTE_PAYLOAD",
                f"{path} contains a non-finite number",
            )
        return value
    if isinstance(value, str):
        if len(value) > _MAX_PAYLOAD_STRING_LENGTH:
            raise RunnerTaskValidationError(
                "ROUTE_PAYLOAD_STRING_TOO_LONG",
                f"{path} contains an oversized string",
            )
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise RunnerTaskValidationError(
                "ROUTE_PAYLOAD_TOO_LARGE",
                f"{path} contains too many fields",
            )
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str) or not _PAYLOAD_KEY_RE.fullmatch(key):
                raise RunnerTaskValidationError(
                    "INVALID_ROUTE_PAYLOAD_KEY",
                    f"{path} contains an invalid key",
                )
            normalized[key] = _freeze_json(
                value[key],
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return MappingProxyType(normalized)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise RunnerTaskValidationError(
                "ROUTE_PAYLOAD_TOO_LARGE",
                f"{path} contains too many items",
            )
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    raise RunnerTaskValidationError(
        "INVALID_ROUTE_PAYLOAD",
        f"{path} contains a non-JSON value",
    )


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RunnerTaskValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be an array",
        )
    if not value:
        raise RunnerTaskValidationError(
            f"EMPTY_{field.upper()}",
            f"{field} must not be empty",
        )
    return value


def _capabilities(value: object) -> tuple[str, ...]:
    items = _sequence(value, "requested_capabilities")
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str) or item not in REQUESTED_CAPABILITIES:
            raise RunnerTaskValidationError(
                "INVALID_REQUESTED_CAPABILITY",
                "requested capability is not allowlisted",
            )
        normalized.append(item)
    _reject_duplicates(normalized, "requested_capabilities")
    return tuple(sorted(normalized))


def _allowed_files(value: object) -> tuple[str, ...]:
    items = _sequence(value, "allowed_files")
    normalized = [_repository_path(item) for item in items]
    _reject_duplicates(normalized, "allowed_files")
    return tuple(sorted(normalized))


def _repository_path(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_PATH_RE.fullmatch(value):
        raise RunnerTaskValidationError(
            "INVALID_ALLOWED_FILE",
            "allowed file must be a bounded repository-relative path",
        )
    if (
        value.startswith(("/", "."))
        or value.endswith("/")
        or "\\" in value
        or "//" in value
        or any(segment in {"", ".", ".."} for segment in value.split("/"))
    ):
        raise RunnerTaskValidationError(
            "INVALID_ALLOWED_FILE",
            "allowed file must be a repository-relative path without traversal",
        )
    return value


def _bounded_text_items(
    value: object,
    *,
    field: str,
    sort_items: bool,
) -> tuple[str, ...]:
    items = _sequence(value, field)
    if len(items) > _MAX_TEXT_ITEMS:
        raise RunnerTaskValidationError(
            f"TOO_MANY_{field.upper()}",
            f"{field} contains too many entries",
        )
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip() or len(item) > _MAX_TEXT_LENGTH:
            raise RunnerTaskValidationError(
                f"INVALID_{field.upper()}",
                f"{field} contains an invalid entry",
            )
        normalized.append(item.strip())
    _reject_duplicates(normalized, field)
    if sort_items:
        normalized.sort()
    return tuple(normalized)


def _validation_commands(value: object) -> tuple[tuple[str, ...], ...]:
    commands = _sequence(value, "validation_commands")
    if len(commands) > _MAX_COMMANDS:
        raise RunnerTaskValidationError(
            "TOO_MANY_VALIDATION_COMMANDS",
            "validation_commands exceeds the command limit",
        )
    normalized: list[tuple[str, ...]] = []
    for command in commands:
        argv = _sequence(command, "validation_command")
        if len(argv) > _MAX_COMMAND_ARGUMENTS:
            raise RunnerTaskValidationError(
                "VALIDATION_COMMAND_TOO_LONG",
                "validation command has too many arguments",
            )
        normalized_argv: list[str] = []
        for argument in argv:
            if (
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
                or len(argument) > _MAX_ARGUMENT_LENGTH
            ):
                raise RunnerTaskValidationError(
                    "INVALID_VALIDATION_COMMAND",
                    "validation command arguments must be bounded strings",
                )
            normalized_argv.append(argument)
        normalized.append(tuple(normalized_argv))
    _reject_duplicates(normalized, "validation_commands")
    return tuple(normalized)


def _validation_timeout(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < MIN_VALIDATION_TIMEOUT_SECONDS
        or value > MAX_VALIDATION_TIMEOUT_SECONDS
    ):
        raise RunnerTaskValidationError(
            "INVALID_VALIDATION_TIMEOUT",
            "validation_timeout_seconds is outside the allowed bounds",
        )
    return value


def _reject_duplicates(values: Sequence[object], field: str) -> None:
    if len(set(values)) != len(values):
        raise RunnerTaskValidationError(
            f"DUPLICATE_{field.upper()}",
            f"{field} contains duplicate entries",
        )
