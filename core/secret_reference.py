from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


SECRET_REFERENCE_SCHEMA = "skeleton.secret_reference.v1"
SECRET_REFERENCE_KIND = "secret_reference"
GOOGLE_SHEETS_CREDENTIALS = "google_sheets_credentials"
ALLOWED_SECRET_TYPES = frozenset({GOOGLE_SHEETS_CREDENTIALS})


class SecretReferenceError(ValueError):
    """Raised when a secret reference or resolver result violates the boundary."""


@dataclass(frozen=True)
class SecretScope:
    project: str
    dataset: str
    repo: str
    branch: str
    task: str

    def __post_init__(self) -> None:
        for field_name, value in asdict(self).items():
            _require_scope_string(field_name, value)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SecretReference:
    secret_type: str
    scope: SecretScope
    schema: str = SECRET_REFERENCE_SCHEMA
    kind: str = SECRET_REFERENCE_KIND

    def __post_init__(self) -> None:
        if self.schema != SECRET_REFERENCE_SCHEMA:
            raise SecretReferenceError("secret reference schema is not supported")
        if self.kind != SECRET_REFERENCE_KIND:
            raise SecretReferenceError("secret reference kind is not supported")
        if self.secret_type not in ALLOWED_SECRET_TYPES:
            raise SecretReferenceError("secret reference type is not allowlisted")
        if not isinstance(self.scope, SecretScope):
            raise SecretReferenceError("secret reference scope must be typed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "kind": self.kind,
            "secret_type": self.secret_type,
            "scope": self.scope.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SecretReference":
        if not isinstance(payload, Mapping):
            raise SecretReferenceError("secret reference must be an object")
        allowed = {"schema", "kind", "secret_type", "scope"}
        extra = set(payload) - allowed
        if extra:
            raise SecretReferenceError("secret reference contains non-typed fields")
        scope_payload = payload.get("scope")
        if not isinstance(scope_payload, Mapping):
            raise SecretReferenceError("secret reference scope must be an object")
        scope_allowed = {"project", "dataset", "repo", "branch", "task"}
        scope_extra = set(scope_payload) - scope_allowed
        if scope_extra:
            raise SecretReferenceError("secret reference scope contains non-typed fields")
        missing_scope = scope_allowed - set(scope_payload)
        if missing_scope:
            raise SecretReferenceError("secret reference scope is incomplete")
        return cls(
            schema=_require_literal(payload.get("schema"), "schema"),
            kind=_require_literal(payload.get("kind"), "kind"),
            secret_type=_require_literal(payload.get("secret_type"), "secret_type"),
            scope=SecretScope(
                project=_require_literal(scope_payload.get("project"), "project"),
                dataset=_require_literal(scope_payload.get("dataset"), "dataset"),
                repo=_require_literal(scope_payload.get("repo"), "repo"),
                branch=_require_literal(scope_payload.get("branch"), "branch"),
                task=_require_literal(scope_payload.get("task"), "task"),
            ),
        )


@dataclass(frozen=True)
class ApprovedSecretResolution:
    reference: SecretReference
    material: object
    approved: bool
    adapter: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, SecretReference):
            raise SecretReferenceError("resolver result must carry a typed secret reference")
        if not isinstance(self.adapter, str) or not self.adapter.strip():
            raise SecretReferenceError("resolver adapter name is required")
        if _looks_like_plaintext_or_location(self.material):
            raise SecretReferenceError("resolver material must be an ephemeral credential object")


def resolve_secret_reference(
    reference: SecretReference,
    resolution: ApprovedSecretResolution | None,
) -> object:
    if not isinstance(reference, SecretReference):
        raise SecretReferenceError("secret reference must be typed")
    if resolution is None:
        raise SecretReferenceError("approved resolver adapter result is required")
    if not isinstance(resolution, ApprovedSecretResolution):
        raise SecretReferenceError("resolver adapter result is not typed")
    if not resolution.approved:
        raise SecretReferenceError("resolver adapter result is not approved")
    if resolution.reference != reference:
        raise SecretReferenceError("resolver adapter result scope does not match the reference")
    if _looks_like_plaintext_or_location(resolution.material):
        raise SecretReferenceError("resolver material must be an ephemeral credential object")
    return resolution.material


def _require_literal(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SecretReferenceError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_scope_string(field_name: str, value: object) -> None:
    text = _require_literal(value, field_name)
    lowered = text.lower()
    forbidden = ("google_application_credentials", "service_account_file", "credentials_path")
    if any(marker in lowered for marker in forbidden):
        raise SecretReferenceError("secret reference scope cannot contain credential material")
    if "\n" in text or "\r" in text or "\x00" in text:
        raise SecretReferenceError("secret reference scope must be a single-line identifier")
    if lowered.startswith(("env:", "file:", "path:")):
        raise SecretReferenceError("secret reference scope cannot contain credential material")


def _looks_like_plaintext_or_location(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, bytes, bytearray, Path, Mapping, list, tuple, set, frozenset)):
        return True
    return False
