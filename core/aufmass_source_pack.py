from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


MANIFEST_SCHEMA = "skeleton.aufmass_source_pack.v1"

SUPPORTED_SOURCE_TYPES = frozenset(
    {
        "dxf",
        "pdf",
        "image_scan",
        "ifc",
        "manual_room_list",
        "operator_note",
        "mixed",
    }
)
SUPPORTED_ARTIFACT_ROUTES = frozenset(
    {
        "public_synthetic",
        "private_drive",
        "private_local_runner",
        "private_operator_handoff",
    }
)
SUPPORTED_PRIVACY_STATUSES = frozenset(
    {
        "synthetic",
        "public_safe",
        "private_pilot",
        "blocked_for_public",
    }
)
SUPPORTED_REVIEW_STATUSES = frozenset(
    {
        "draft",
        "needs_review",
        "reviewed",
        "rejected",
        "approved_for_private_intake",
    }
)
SUPPORTED_SCALE_BASIS = frozenset(
    {
        "known_dimension",
        "drawing_scale",
        "declared_model_units",
        "not_applicable",
        "unknown",
    }
)

GEOMETRIC_SOURCE_TYPES = frozenset({"dxf", "pdf", "image_scan", "ifc", "mixed"})
PRIVATE_ROUTES = frozenset({"private_drive", "private_local_runner", "private_operator_handoff"})
PUBLIC_ROUTES = frozenset({"public_synthetic"})
FORBIDDEN_REFERENCE_FRAGMENTS = (
    "://",
    "drive.google",
    "docs.google",
    "file id",
    "folder id",
    "customer",
    "address",
)


@dataclass(frozen=True)
class AufmassScaleHint:
    basis: str
    detail: Optional[str] = None


@dataclass(frozen=True)
class AufmassSourceMetadata:
    title: str
    source_revision: str
    prepared_by: str


@dataclass(frozen=True)
class AufmassSourceReference:
    source_id: str
    source_type: str
    artifact_ref: str
    artifact_route: str
    metadata: AufmassSourceMetadata
    scale_hint: AufmassScaleHint
    privacy_status: str
    review_status: str
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AufmassSourcePackManifest:
    pack_id: str
    project_id: str
    sources: list[AufmassSourceReference]
    schema: str = MANIFEST_SCHEMA


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)


def validate_source_pack_manifest(data: Mapping[str, Any]) -> ValidationResult:
    """Validate declared Aufmass intake metadata without opening source files."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    _require_string(data, "schema", "$.schema", errors)
    _require_string(data, "pack_id", "$.pack_id", errors)
    _require_string(data, "project_id", "$.project_id", errors)

    if data.get("schema") != MANIFEST_SCHEMA:
        _error(errors, "$.schema", "invalid_schema", f"schema must be {MANIFEST_SCHEMA}.")

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        _error(errors, "$.sources", "missing_sources", "sources must contain at least one source.")
    elif isinstance(sources, list):
        seen_source_ids: set[str] = set()
        for index, source in enumerate(sources):
            path = f"$.sources[{index}]"
            if not isinstance(source, Mapping):
                _error(errors, path, "invalid_source", "source must be an object.")
                continue
            source_id = _validate_source(source, path, errors, warnings)
            if source_id:
                if source_id in seen_source_ids:
                    _error(errors, f"{path}.source_id", "duplicate_source_id", "source_id must be unique.")
                seen_source_ids.add(source_id)

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def source_pack_manifest_from_dict(data: Mapping[str, Any]) -> AufmassSourcePackManifest:
    """Build the dataclass model after validation has succeeded."""
    result = validate_source_pack_manifest(data)
    if not result.ok:
        messages = ", ".join(f"{issue.path}: {issue.message}" for issue in result.errors)
        raise ValueError(messages)

    return AufmassSourcePackManifest(
        schema=str(data["schema"]),
        pack_id=str(data["pack_id"]),
        project_id=str(data["project_id"]),
        sources=[_source_from_dict(source) for source in data["sources"]],
    )


def _validate_source(
    source: Mapping[str, Any],
    path: str,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> Optional[str]:
    source_id = _require_string(source, "source_id", f"{path}.source_id", errors)
    source_type = _require_string(source, "source_type", f"{path}.source_type", errors)
    artifact_ref = _require_string(source, "artifact_ref", f"{path}.artifact_ref", errors)
    artifact_route = _require_string(source, "artifact_route", f"{path}.artifact_route", errors)
    privacy_status = _require_string(source, "privacy_status", f"{path}.privacy_status", errors)
    review_status = _require_string(source, "review_status", f"{path}.review_status", errors)

    if source_type and source_type not in SUPPORTED_SOURCE_TYPES:
        _error(errors, f"{path}.source_type", "unsupported_source_type", "source_type is not supported.")
    if artifact_route and artifact_route not in SUPPORTED_ARTIFACT_ROUTES:
        _error(errors, f"{path}.artifact_route", "unsupported_artifact_route", "artifact_route is not supported.")
    if privacy_status and privacy_status not in SUPPORTED_PRIVACY_STATUSES:
        _error(errors, f"{path}.privacy_status", "unsupported_privacy_status", "privacy_status is not supported.")
    if review_status and review_status not in SUPPORTED_REVIEW_STATUSES:
        _error(errors, f"{path}.review_status", "unsupported_review_status", "review_status is not supported.")

    if artifact_ref:
        _validate_artifact_ref(artifact_ref, f"{path}.artifact_ref", errors)
    if source_id and not _is_token(source_id):
        _error(errors, f"{path}.source_id", "invalid_source_id", "source_id must be an opaque token.")

    metadata = source.get("metadata")
    if not isinstance(metadata, Mapping):
        _error(errors, f"{path}.metadata", "missing_metadata", "metadata must be an object.")
    else:
        for key in ("title", "source_revision", "prepared_by"):
            _require_string(metadata, key, f"{path}.metadata.{key}", errors)

    scale_hint = source.get("scale_hint")
    if not isinstance(scale_hint, Mapping):
        _error(errors, f"{path}.scale_hint", "missing_scale_hint", "scale_hint must be an object.")
    else:
        basis = _require_string(scale_hint, "basis", f"{path}.scale_hint.basis", errors)
        if basis and basis not in SUPPORTED_SCALE_BASIS:
            _error(errors, f"{path}.scale_hint.basis", "unsupported_scale_basis", "scale hint basis is not supported.")
        detail = scale_hint.get("detail")
        if basis in {"known_dimension", "drawing_scale", "declared_model_units"} and not _non_empty_string(detail):
            _error(errors, f"{path}.scale_hint.detail", "missing_scale_detail", "scale detail is required for this basis.")
        if basis == "unknown" and source_type in GEOMETRIC_SOURCE_TYPES:
            _warning(warnings, f"{path}.scale_hint.basis", "scale_needs_review", "geometric source has unknown scale.")
        if basis == "not_applicable" and source_type in GEOMETRIC_SOURCE_TYPES:
            _error(errors, f"{path}.scale_hint.basis", "scale_required", "geometric source requires a scale or calibration hint.")

    _validate_privacy_route(path, artifact_route, privacy_status, errors)
    _validate_review_status(path, review_status, warnings)

    return source_id


def _validate_artifact_ref(ref: str, path: str, errors: list[ValidationIssue]) -> None:
    lowered = ref.lower()
    if not _is_token(ref):
        _error(errors, path, "invalid_artifact_ref", "artifact_ref must be an opaque token, not a path or URL.")
    for fragment in FORBIDDEN_REFERENCE_FRAGMENTS:
        if fragment in lowered:
            _error(errors, path, "private_reference_leak", "artifact_ref contains private route details.")


def _validate_privacy_route(
    path: str,
    artifact_route: Optional[str],
    privacy_status: Optional[str],
    errors: list[ValidationIssue],
) -> None:
    if privacy_status == "private_pilot" and artifact_route not in PRIVATE_ROUTES:
        _error(errors, f"{path}.artifact_route", "private_route_required", "private_pilot sources require an approved private route.")
    if privacy_status in {"synthetic", "public_safe"} and artifact_route not in PUBLIC_ROUTES:
        _error(errors, f"{path}.artifact_route", "public_route_required", "public-safe sources must use the public synthetic route.")


def _validate_review_status(
    path: str,
    review_status: Optional[str],
    warnings: list[ValidationIssue],
) -> None:
    if review_status in {"draft", "needs_review"}:
        _warning(warnings, f"{path}.review_status", "review_not_final", "source is not yet approved for intake.")


def _source_from_dict(source: Mapping[str, Any]) -> AufmassSourceReference:
    metadata = source["metadata"]
    scale_hint = source["scale_hint"]
    return AufmassSourceReference(
        source_id=str(source["source_id"]),
        source_type=str(source["source_type"]),
        artifact_ref=str(source["artifact_ref"]),
        artifact_route=str(source["artifact_route"]),
        metadata=AufmassSourceMetadata(
            title=str(metadata["title"]),
            source_revision=str(metadata["source_revision"]),
            prepared_by=str(metadata["prepared_by"]),
        ),
        scale_hint=AufmassScaleHint(
            basis=str(scale_hint["basis"]),
            detail=str(scale_hint["detail"]) if scale_hint.get("detail") is not None else None,
        ),
        privacy_status=str(source["privacy_status"]),
        review_status=str(source["review_status"]),
        notes=[str(note) for note in source.get("notes", [])],
    )


def _require_string(
    data: Mapping[str, Any],
    key: str,
    path: str,
    errors: list[ValidationIssue],
) -> Optional[str]:
    value = data.get(key)
    if not _non_empty_string(value):
        _error(errors, path, "missing_required", f"{key} is required.")
        return None
    return str(value)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_token(value: str) -> bool:
    return bool(value) and all(character.isalnum() or character in "._-" for character in value)


def _error(errors: list[ValidationIssue], path: str, code: str, message: str) -> None:
    errors.append(ValidationIssue(severity="error", path=path, code=code, message=message))


def _warning(warnings: list[ValidationIssue], path: str, code: str, message: str) -> None:
    warnings.append(ValidationIssue(severity="warning", path=path, code=code, message=message))
