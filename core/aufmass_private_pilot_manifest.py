from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


MANIFEST_SCHEMA = "skeleton.aufmass_private_pilot_manifest.v1"

APPROVED_ROUTE_STAGES = (
    "input_sources",
    "extracted_candidates",
    "room_review_table",
    "operator_corrections",
    "private_exports",
    "public_safe_lessons",
)
APPROVED_SOURCE_TYPES = frozenset(
    {
        "dxf",
        "pdf",
        "image_scan",
        "manual_room_list",
        "operator_note",
        "mixed",
    }
)
APPROVED_ARTIFACT_KINDS = frozenset(
    {
        "input_source",
        "extracted_candidate",
        "room_review_table",
        "operator_correction",
        "private_export",
        "public_safe_lesson",
    }
)
APPROVED_PRIVATE_ROUTES = frozenset(
    {
        "private_drive",
        "private_local_runner",
        "private_operator_handoff",
    }
)
PUBLIC_ROUTES = frozenset({"public_synthetic", "public_safe_lessons"})
APPROVED_PUBLIC_SAFETY_STATUSES = frozenset(
    {
        "private_only",
        "anonymized",
        "synthetic",
        "blocked_for_public",
    }
)
APPROVED_REVIEW_STATUSES = frozenset(
    {
        "needs_review",
        "reviewed",
        "rejected",
        "export_ready",
    }
)
NON_FINAL_REVIEW_STATUSES = frozenset({"needs_review"})
FORBIDDEN_REF_FRAGMENTS = (
    "://",
    "\\",
    "/",
    "~",
    "drive.google",
    "docs.google",
    "file id",
    "folder id",
    "secret",
    "token",
    "customer",
    "address",
)


@dataclass(frozen=True)
class PrivatePilotArtifactRef:
    private_ref: str
    source_type: str
    review_stage: str
    artifact_kind: str
    artifact_route: str
    review_status: str
    public_safety_status: str


@dataclass(frozen=True)
class PrivatePilotRoutedRef:
    ref: str
    route: str


@dataclass(frozen=True)
class PublicSafety:
    status: str


@dataclass(frozen=True)
class AufmassPrivatePilotManifest:
    pilot_id: str
    project_id: str
    route_stage: str
    private_refs: list[PrivatePilotArtifactRef]
    public_safety: PublicSafety
    notes: list[str] = field(default_factory=list)
    output_refs: list[PrivatePilotRoutedRef] = field(default_factory=list)
    report_refs: list[PrivatePilotRoutedRef] = field(default_factory=list)
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


def validate_private_pilot_manifest(data: Mapping[str, Any]) -> ValidationResult:
    """Validate public-safe Aufmass private pilot metadata without resolving artifacts."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    _require_string(data, "schema", "$.schema", errors)
    _require_string(data, "pilot_id", "$.pilot_id", errors)
    _require_string(data, "project_id", "$.project_id", errors)
    route_stage = _require_string(data, "route_stage", "$.route_stage", errors)

    if data.get("schema") != MANIFEST_SCHEMA:
        _error(errors, "$.schema", "invalid_schema", f"schema must be {MANIFEST_SCHEMA}.")
    if data.get("project_id") != "aufmass":
        _error(errors, "$.project_id", "invalid_project_id", "project_id must be aufmass.")
    if route_stage and route_stage not in APPROVED_ROUTE_STAGES:
        _error(errors, "$.route_stage", "unsupported_route_stage", "route_stage is not approved.")
    if route_stage in PUBLIC_ROUTES:
        _error(errors, "$.route_stage", "public_route_for_private_artifacts", "private pilot artifacts must not use a public route.")

    pilot_id = data.get("pilot_id")
    if isinstance(pilot_id, str) and (not pilot_id.startswith("private-pilot-") or not _is_token(pilot_id)):
        _error(errors, "$.pilot_id", "invalid_pilot_id", "pilot_id must be an opaque token.")

    private_refs = data.get("private_refs")
    if not isinstance(private_refs, list) or not private_refs:
        _error(errors, "$.private_refs", "missing_private_refs", "private_refs must contain at least one private reference.")
    else:
        seen_refs: set[str] = set()
        for index, private_ref in enumerate(private_refs):
            path = f"$.private_refs[{index}]"
            if not isinstance(private_ref, Mapping):
                _error(errors, path, "invalid_private_ref", "private_ref entry must be an object.")
                continue
            ref = _validate_private_ref(private_ref, path, errors, warnings)
            if ref:
                if ref in seen_refs:
                    _error(errors, f"{path}.private_ref", "duplicate_private_ref", "private_ref must be unique.")
                seen_refs.add(ref)

    public_safety = data.get("public_safety")
    if not isinstance(public_safety, Mapping):
        _error(errors, "$.public_safety", "missing_public_safety", "public_safety must be an object.")
    else:
        status = _require_string(public_safety, "status", "$.public_safety.status", errors)
        if status and status not in APPROVED_PUBLIC_SAFETY_STATUSES:
            _error(errors, "$.public_safety.status", "unsupported_public_safety_status", "public_safety status is not supported.")

    _validate_notes(data.get("notes"), "$.notes", errors)
    _validate_routed_refs(data.get("output_refs", []), "$.output_refs", errors)
    _validate_routed_refs(data.get("report_refs", []), "$.report_refs", errors)

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def private_pilot_manifest_from_dict(data: Mapping[str, Any]) -> AufmassPrivatePilotManifest:
    """Build the dataclass model after validation has succeeded."""
    result = validate_private_pilot_manifest(data)
    if not result.ok:
        messages = ", ".join(f"{issue.path}: {issue.message}" for issue in result.errors)
        raise ValueError(messages)

    public_safety = data["public_safety"]
    return AufmassPrivatePilotManifest(
        schema=str(data["schema"]),
        pilot_id=str(data["pilot_id"]),
        project_id=str(data["project_id"]),
        route_stage=str(data["route_stage"]),
        private_refs=[_private_ref_from_dict(ref) for ref in data["private_refs"]],
        public_safety=PublicSafety(status=str(public_safety["status"])),
        notes=[str(note) for note in data.get("notes", [])],
        output_refs=[_routed_ref_from_dict(ref) for ref in data.get("output_refs", [])],
        report_refs=[_routed_ref_from_dict(ref) for ref in data.get("report_refs", [])],
    )


def _validate_private_ref(
    private_ref: Mapping[str, Any],
    path: str,
    errors: list[ValidationIssue],
    warnings: list[ValidationIssue],
) -> Optional[str]:
    ref = _require_string(private_ref, "private_ref", f"{path}.private_ref", errors)
    source_type = _require_string(private_ref, "source_type", f"{path}.source_type", errors)
    review_stage = _require_string(private_ref, "review_stage", f"{path}.review_stage", errors)
    artifact_kind = _require_string(private_ref, "artifact_kind", f"{path}.artifact_kind", errors)
    artifact_route = _require_string(private_ref, "artifact_route", f"{path}.artifact_route", errors)
    review_status = _require_string(private_ref, "review_status", f"{path}.review_status", errors)
    public_safety_status = _require_string(private_ref, "public_safety_status", f"{path}.public_safety_status", errors)

    if ref:
        _validate_opaque_ref(ref, f"{path}.private_ref", errors)
    if source_type and source_type not in APPROVED_SOURCE_TYPES:
        _error(errors, f"{path}.source_type", "unsupported_source_type", "source_type is not approved.")
    if review_stage and review_stage not in APPROVED_ROUTE_STAGES:
        _error(errors, f"{path}.review_stage", "unsupported_review_stage", "review_stage is not approved.")
    if review_stage in PUBLIC_ROUTES:
        _error(errors, f"{path}.review_stage", "public_route_for_private_artifacts", "private pilot artifacts must not use a public route.")
    if artifact_kind and artifact_kind not in APPROVED_ARTIFACT_KINDS:
        _error(errors, f"{path}.artifact_kind", "unsupported_artifact_kind", "artifact_kind is not approved.")
    if artifact_route and artifact_route not in APPROVED_PRIVATE_ROUTES:
        _error(errors, f"{path}.artifact_route", "unsupported_private_route", "artifact_route must be an approved private route.")
    if review_status and review_status not in APPROVED_REVIEW_STATUSES:
        _error(errors, f"{path}.review_status", "unsupported_review_status", "review_status is not supported.")
    if review_status in NON_FINAL_REVIEW_STATUSES:
        _warning(warnings, f"{path}.review_status", "review_not_final", "private artifact review is not final.")
    if public_safety_status and public_safety_status not in APPROVED_PUBLIC_SAFETY_STATUSES:
        _error(errors, f"{path}.public_safety_status", "unsupported_public_safety_status", "public_safety_status is not supported.")

    return ref


def _validate_routed_refs(value: Any, path: str, errors: list[ValidationIssue]) -> None:
    if not isinstance(value, list):
        _error(errors, path, "invalid_routed_refs", "routed references must be an array.")
        return
    for index, routed_ref in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(routed_ref, Mapping):
            _error(errors, item_path, "invalid_routed_ref", "routed reference must be an object.")
            continue
        ref = _require_string(routed_ref, "ref", f"{item_path}.ref", errors)
        route = _require_string(routed_ref, "route", f"{item_path}.route", errors)
        if ref:
            _validate_opaque_ref(ref, f"{item_path}.ref", errors)
        if route and route not in APPROVED_PRIVATE_ROUTES:
            _error(errors, f"{item_path}.route", "unsupported_private_route", "output and report references must use an approved private route.")


def _validate_notes(value: Any, path: str, errors: list[ValidationIssue]) -> None:
    if not isinstance(value, list):
        _error(errors, path, "invalid_notes", "notes must be an array.")
        return
    for index, note in enumerate(value):
        if not isinstance(note, str):
            _error(errors, f"{path}[{index}]", "invalid_note", "note must be a string.")


def _validate_opaque_ref(ref: str, path: str, errors: list[ValidationIssue]) -> None:
    lowered = ref.lower()
    if not ref.startswith("private-ref-") or not _is_token(ref):
        _error(errors, path, "invalid_private_ref", "reference must be an opaque private-ref token, not a path or URL.")
    for fragment in FORBIDDEN_REF_FRAGMENTS:
        if fragment in lowered:
            _error(errors, path, "private_reference_leak", "reference contains private route details.")


def _private_ref_from_dict(private_ref: Mapping[str, Any]) -> PrivatePilotArtifactRef:
    return PrivatePilotArtifactRef(
        private_ref=str(private_ref["private_ref"]),
        source_type=str(private_ref["source_type"]),
        review_stage=str(private_ref["review_stage"]),
        artifact_kind=str(private_ref["artifact_kind"]),
        artifact_route=str(private_ref["artifact_route"]),
        review_status=str(private_ref["review_status"]),
        public_safety_status=str(private_ref["public_safety_status"]),
    )


def _routed_ref_from_dict(routed_ref: Mapping[str, Any]) -> PrivatePilotRoutedRef:
    return PrivatePilotRoutedRef(ref=str(routed_ref["ref"]), route=str(routed_ref["route"]))


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
