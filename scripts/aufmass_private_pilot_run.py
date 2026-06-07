from __future__ import annotations

import argparse
import csv
import importlib
import json
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from core.aufmass_dxf_adapter import dxf_result_to_dict, extract_dxf
from core.aufmass_engine import calculate_aufmass
from core.aufmass_exporter import aufmass_result_to_csv, aufmass_result_to_json_dict
from core.aufmass_manual_adapter import (
    ManualAufmassInput,
    ManualOpeningInput,
    ManualPoint,
    ManualRoomInput,
    ScaleCalibration,
    convert_manual_plan,
)
from core.aufmass_room_matcher import match_dxf_rooms, room_match_result_to_dict
from core.aufmass_room_review import room_review_table_to_dict, room_matches_to_review_table
from core.aufmass_source_pack import (
    ValidationIssue,
    source_pack_manifest_from_dict,
    validate_source_pack_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK_FILENAME = "source_pack_manifest.json"
PRIVATE_PILOT_SCHEMA = "skeleton.aufmass_private_pilot_manifest.v1"
PUBLIC_SAFE_SUMMARY_SCHEMA = "skeleton.aufmass_private_pilot_public_summary.v1"
SUPPORTED_BRANCHES = ("manual-only", "dxf-assisted")
PUBLIC_ROUTE_FRAGMENTS = (
    ".git",
    "github",
    "skeleton",
    "public",
    "repo",
)
FORBIDDEN_REF_FRAGMENTS = (
    "://",
    "\\",
    "/",
    "..",
    "~",
    "drive.google",
    "docs.google",
    "file id",
    "folder id",
)
PRIVATE_PILOT_ROUTE_STAGES = frozenset(
    {
        "input_sources",
        "extracted_candidates",
        "room_review_table",
        "operator_corrections",
        "private_exports",
        "public_safe_lessons",
    }
)
PRIVATE_PILOT_ARTIFACT_KINDS = frozenset(
    {
        "input_source",
        "extracted_candidate",
        "room_review_table",
        "operator_correction",
        "private_export",
        "public_safe_lesson",
    }
)
PRIVATE_PILOT_SAFETY_STATUSES = frozenset(
    {"private_only", "anonymized", "synthetic", "blocked_for_public"}
)


class PilotError(RuntimeError):
    """Public-safe orchestration failure."""


def plan_private_pilot_run(args: argparse.Namespace) -> dict[str, object]:
    source_pack_path = _require_file(args.source_pack_manifest, "source pack manifest")
    if source_pack_path.name != SOURCE_PACK_FILENAME:
        raise PilotError(f"source pack manifest must be named {SOURCE_PACK_FILENAME}.")

    source_pack_data = _read_json_object(source_pack_path, "source pack manifest")
    source_pack_validation = validate_source_pack_manifest(source_pack_data)
    if not source_pack_validation.ok:
        raise PilotError(
            "source pack validation failed: "
            + _issue_codes(source_pack_validation.errors)
        )
    source_pack = source_pack_manifest_from_dict(source_pack_data)

    private_manifest_status = "not_requested"
    if args.private_pilot_manifest:
        private_manifest_path = _require_file(args.private_pilot_manifest, "private pilot manifest")
        private_manifest = _read_json_object(private_manifest_path, "private pilot manifest")
        _validate_private_pilot_manifest(private_manifest)
        private_manifest_status = "validated"

    branch = _select_branch(args.branch, source_pack.sources)
    selected_sources = [
        source.source_id
        for source in source_pack.sources
        if _source_applies_to_branch(source.source_type, branch)
    ]
    if not selected_sources:
        raise PilotError(f"no source pack sources are compatible with branch {branch}.")

    plan = {
        "schema": PUBLIC_SAFE_SUMMARY_SCHEMA,
        "mode": "dry-run" if not args.execute else "execute",
        "branch": branch,
        "pack_id": source_pack.pack_id,
        "project_id": source_pack.project_id,
        "source_count": len(source_pack.sources),
        "selected_source_count": len(selected_sources),
        "selected_source_ids": selected_sources,
        "source_validation": {
            "errors": [],
            "warnings": [_issue_to_public_dict(issue) for issue in source_pack_validation.warnings],
        },
        "private_pilot_manifest": private_manifest_status,
        "private_artifacts": _planned_artifacts(branch, args.manual_input is not None),
        "safety": {
            "dry_run_default": True,
            "private_workspace_required_for_execute": True,
            "private_paths_redacted": not args.local_debug,
            "network_calls": False,
            "drive_api_calls": False,
            "gemini_calls": False,
        },
    }

    if branch == "manual-only":
        plan["manual_handoff"] = _manual_handoff_plan(args.manual_input is not None)
    if branch == "dxf-assisted":
        plan["dxf_assisted"] = {
            "requires_ezdxf": True,
            "extract_room_candidates": True,
            "build_room_review_table": True,
        }

    return plan


def execute_private_pilot_run(args: argparse.Namespace) -> dict[str, object]:
    plan = plan_private_pilot_run(args)
    private_workspace = _require_private_workspace(args.private_workspace)
    output_root = _require_private_output_root(args.output_root, private_workspace)
    output_root.mkdir(parents=True, exist_ok=True)

    if plan["branch"] == "manual-only":
        artifact_summary = _execute_manual_branch(args, private_workspace, output_root)
    elif plan["branch"] == "dxf-assisted":
        artifact_summary = _execute_dxf_branch(args, private_workspace, output_root)
    else:  # pragma: no cover - argparse and planning constrain this.
        raise PilotError("unsupported branch.")

    plan["mode"] = "execute"
    plan["private_artifact_status"] = artifact_summary
    return plan


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.execute:
            summary = execute_private_pilot_run(args)
        else:
            summary = plan_private_pilot_run(args)

        if args.public_summary:
            public_summary_path = Path(args.public_summary).expanduser()
            _write_public_summary(public_summary_path, summary)

        if args.local_debug:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print(json.dumps(_redact_summary(summary), sort_keys=True))
        return 0
    except PilotError as exc:
        print(f"aufmass private pilot refused: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Public-safe planner/orchestrator for controlled private Aufmass pilot runs.",
    )
    parser.add_argument("--source-pack-manifest", required=True)
    parser.add_argument("--branch", choices=SUPPORTED_BRANCHES, default="manual-only")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--private-workspace")
    parser.add_argument("--output-root")
    parser.add_argument("--manual-input")
    parser.add_argument("--artifact-map")
    parser.add_argument("--private-pilot-manifest")
    parser.add_argument("--public-summary")
    parser.add_argument("--local-debug", action="store_true")
    return parser


def _execute_manual_branch(
    args: argparse.Namespace,
    private_workspace: Path,
    output_root: Path,
) -> dict[str, object]:
    if not args.manual_input:
        raise PilotError("manual-only execution requires --manual-input.")
    manual_input_path = _require_private_file(args.manual_input, private_workspace, "manual input")
    manual_input = _manual_input_from_dict(_read_json_object(manual_input_path, "manual input"))

    adapter_result = convert_manual_plan(manual_input)
    aufmass_result = calculate_aufmass(adapter_result.aufmass_input)

    _write_json(output_root / "manual_adapter_audit.json", asdict(adapter_result.audit))
    _write_json(output_root / "aufmass_export.json", aufmass_result_to_json_dict(aufmass_result))
    (output_root / "aufmass_export.csv").write_text(
        aufmass_result_to_csv(aufmass_result),
        encoding="utf-8",
    )

    return {
        "artifact_count": 3,
        "artifact_kinds": ["manual_adapter_audit", "aufmass_export_json", "aufmass_export_csv"],
    }


def _execute_dxf_branch(
    args: argparse.Namespace,
    private_workspace: Path,
    output_root: Path,
) -> dict[str, object]:
    _require_ezdxf()
    artifact_map = _load_artifact_map(args.artifact_map, private_workspace)
    source_pack_data = _read_json_object(Path(args.source_pack_manifest), "source pack manifest")
    source_pack = source_pack_manifest_from_dict(source_pack_data)
    dxf_sources = [source for source in source_pack.sources if source.source_type == "dxf"]
    if not dxf_sources:
        raise PilotError("dxf-assisted execution requires at least one dxf source.")

    artifacts = []
    for source in dxf_sources:
        dxf_path = _artifact_path(source.artifact_ref, artifact_map, private_workspace)
        dxf_result = extract_dxf(dxf_path)
        room_matches = match_dxf_rooms(dxf_result)
        review_table = room_matches_to_review_table(room_matches)

        stem = source.source_id
        _write_json(output_root / f"{stem}_dxf_extract.json", dxf_result_to_dict(dxf_result))
        _write_json(output_root / f"{stem}_room_matches.json", room_match_result_to_dict(room_matches))
        review_table_dict = room_review_table_to_dict(review_table)
        _write_json(output_root / f"{stem}_room_review_table.json", review_table_dict)
        _write_review_csv(output_root / f"{stem}_room_review_table.csv", review_table_dict)
        artifacts.extend(["dxf_extract", "room_matches", "room_review_table_json", "room_review_table_csv"])

    return {
        "artifact_count": len(artifacts),
        "artifact_kinds": sorted(set(artifacts)),
        "dxf_source_count": len(dxf_sources),
    }


def _require_ezdxf() -> None:
    try:
        importlib.import_module("ezdxf")
    except ImportError as exc:
        raise PilotError("dxf-assisted execution requires ezdxf before extraction.") from exc


def _select_branch(branch: str, sources: Sequence[object]) -> str:
    if branch not in SUPPORTED_BRANCHES:
        raise PilotError("unsupported branch.")
    source_types = {getattr(source, "source_type", "") for source in sources}
    if branch == "dxf-assisted" and "dxf" not in source_types:
        raise PilotError("dxf-assisted branch requires at least one dxf source.")
    return branch


def _source_applies_to_branch(source_type: str, branch: str) -> bool:
    if branch == "dxf-assisted":
        return source_type == "dxf"
    return source_type in {"manual_room_list", "operator_note", "dxf", "mixed"}


def _planned_artifacts(branch: str, has_manual_input: bool) -> list[str]:
    if branch == "dxf-assisted":
        return ["dxf_extract", "room_matches", "room_review_table"]
    if has_manual_input:
        return ["manual_input_handoff", "aufmass_engine_export"]
    return ["manual_input_handoff_check"]


def _manual_handoff_plan(has_manual_input: bool) -> dict[str, object]:
    return {
        "requires_operator_manual_input": True,
        "manual_input_provided": has_manual_input,
        "engine_export_helper_ready": has_manual_input,
        "execution_requires_manual_input": True,
    }


def _validate_private_pilot_manifest(data: Mapping[str, Any]) -> None:
    required = {"schema", "project_id", "route_stage", "private_refs", "public_safety", "notes"}
    missing = sorted(required - set(data))
    if missing:
        raise PilotError("private pilot manifest missing required fields: " + ", ".join(missing))
    if data.get("schema") != PRIVATE_PILOT_SCHEMA:
        raise PilotError("private pilot manifest schema is unsupported.")
    if data.get("project_id") != "aufmass":
        raise PilotError("private pilot manifest project_id must be aufmass.")
    if data.get("route_stage") not in PRIVATE_PILOT_ROUTE_STAGES:
        raise PilotError("private pilot manifest route_stage is unsupported.")

    safety = data.get("public_safety")
    if not isinstance(safety, Mapping) or safety.get("status") not in PRIVATE_PILOT_SAFETY_STATUSES:
        raise PilotError("private pilot manifest public_safety status is unsupported.")

    private_refs = data.get("private_refs")
    if not isinstance(private_refs, list):
        raise PilotError("private pilot manifest private_refs must be a list.")
    for index, private_ref in enumerate(private_refs):
        if not isinstance(private_ref, Mapping):
            raise PilotError(f"private pilot manifest private_refs[{index}] must be an object.")
        token = private_ref.get("private_ref")
        if not isinstance(token, str) or not token.startswith("private-ref-") or _looks_like_path_or_url(token):
            raise PilotError(f"private pilot manifest private_refs[{index}] contains unsafe private_ref.")
        if private_ref.get("review_stage") not in PRIVATE_PILOT_ROUTE_STAGES:
            raise PilotError(f"private pilot manifest private_refs[{index}] has unsupported review_stage.")
        if private_ref.get("artifact_kind") not in PRIVATE_PILOT_ARTIFACT_KINDS:
            raise PilotError(f"private pilot manifest private_refs[{index}] has unsupported artifact_kind.")
        if private_ref.get("public_safety_status") not in PRIVATE_PILOT_SAFETY_STATUSES:
            raise PilotError(f"private pilot manifest private_refs[{index}] has unsupported public_safety_status.")

    notes = data.get("notes")
    if not isinstance(notes, list) or any(not isinstance(note, str) or len(note) > 240 for note in notes):
        raise PilotError("private pilot manifest notes must be short strings.")
    if _contains_forbidden_reference(data):
        raise PilotError("private pilot manifest contains a URL-like or path-like reference.")


def _manual_input_from_dict(data: Mapping[str, Any]) -> ManualAufmassInput:
    calibration = data.get("calibration")
    if not isinstance(calibration, Mapping):
        raise PilotError("manual input calibration is required.")
    rooms = data.get("rooms")
    if not isinstance(rooms, list):
        raise PilotError("manual input rooms must be a list.")
    return ManualAufmassInput(
        project_id=str(data["project_id"]),
        calibration=ScaleCalibration(
            point_a=_manual_point(calibration["point_a"]),
            point_b=_manual_point(calibration["point_b"]),
            real_length_m=float(calibration["real_length_m"]),
            source_page=_optional_string(calibration.get("source_page")),
            source_ref=_optional_string(calibration.get("source_ref")),
            confidence=_optional_float(calibration.get("confidence")),
            review_status=_optional_string(calibration.get("review_status")),
        ),
        rooms=[_manual_room(room) for room in rooms],
        source_page=_optional_string(data.get("source_page")),
        source_ref=_optional_string(data.get("source_ref")),
        confidence=_optional_float(data.get("confidence")),
        review_status=_optional_string(data.get("review_status")),
    )


def _manual_room(data: Any) -> ManualRoomInput:
    if not isinstance(data, Mapping):
        raise PilotError("manual input room entries must be objects.")
    openings = data.get("openings", [])
    if not isinstance(openings, list):
        raise PilotError("manual input room openings must be a list.")
    return ManualRoomInput(
        room_id=str(data["room_id"]),
        height_m=float(data["height_m"]),
        polygon=[_manual_point(point) for point in data["polygon"]],
        openings=[_manual_opening(opening) for opening in openings],
        name=_optional_string(data.get("name")),
        source_page=_optional_string(data.get("source_page")),
        source_ref=_optional_string(data.get("source_ref")),
        confidence=_optional_float(data.get("confidence")),
        review_status=_optional_string(data.get("review_status")),
    )


def _manual_opening(data: Any) -> ManualOpeningInput:
    if not isinstance(data, Mapping):
        raise PilotError("manual input opening entries must be objects.")
    return ManualOpeningInput(
        width=float(data["width"]),
        height=float(data["height"]),
        count=int(data.get("count", 1)),
        opening_id=_optional_string(data.get("opening_id")),
        name=_optional_string(data.get("name")),
        dimension_unit=str(data.get("dimension_unit", "drawing")),
        source_page=_optional_string(data.get("source_page")),
        source_ref=_optional_string(data.get("source_ref")),
        confidence=_optional_float(data.get("confidence")),
        review_status=_optional_string(data.get("review_status")),
    )


def _manual_point(data: Any) -> ManualPoint:
    if not isinstance(data, Mapping):
        raise PilotError("manual input points must be objects.")
    return ManualPoint(x=float(data["x"]), y=float(data["y"]))


def _load_artifact_map(path: str | None, private_workspace: Path) -> Mapping[str, str]:
    if not path:
        raise PilotError("dxf-assisted execution requires --artifact-map.")
    artifact_map_path = _require_private_file(path, private_workspace, "artifact map")
    data = _read_json_object(artifact_map_path, "artifact map")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
        raise PilotError("artifact map must be an object of artifact_ref to private relative path.")
    for key, value in data.items():
        if _looks_like_path_or_url(key):
            raise PilotError("artifact map keys must be opaque artifact_ref tokens.")
        if _looks_like_url(value) or Path(value).is_absolute():
            raise PilotError("artifact map values must be private workspace relative paths.")
    return data  # type: ignore[return-value]


def _artifact_path(artifact_ref: str, artifact_map: Mapping[str, str], private_workspace: Path) -> Path:
    relative = artifact_map.get(artifact_ref)
    if relative is None:
        raise PilotError("artifact map is missing a source artifact_ref.")
    return _require_private_file(private_workspace / relative, private_workspace, "source artifact")


def _require_file(path: str | Path, label: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    if _looks_like_url(str(path)):
        raise PilotError(f"{label} must be a local file.")
    if not candidate.is_file():
        raise PilotError(f"{label} is missing.")
    return candidate


def _require_private_file(path: str | Path, private_workspace: Path, label: str) -> Path:
    candidate = _require_file(path, label)
    if not _is_relative_to(candidate, private_workspace):
        raise PilotError(f"{label} must be inside the private workspace.")
    return candidate


def _require_private_workspace(path: str | None) -> Path:
    if not path:
        raise PilotError("execution requires --private-workspace.")
    workspace = Path(path).expanduser().resolve()
    if _looks_like_url(path):
        raise PilotError("private workspace must be a local path.")
    if not workspace.is_dir():
        raise PilotError("private workspace is missing.")
    if _is_relative_to(workspace, ROOT):
        raise PilotError("private workspace must not be inside the public repository.")
    return workspace


def _require_private_output_root(path: str | None, private_workspace: Path) -> Path:
    if not path:
        raise PilotError("execution requires --output-root.")
    output_root = Path(path).expanduser().resolve()
    if _looks_like_url(path):
        raise PilotError("output root must be a local path.")
    if _is_relative_to(output_root, ROOT):
        raise PilotError("private artifacts must not be written inside the public repository.")
    if not _is_relative_to(output_root, private_workspace):
        raise PilotError("output root must be inside the explicit private workspace.")
    if any(fragment in {part.lower() for part in output_root.parts} for fragment in PUBLIC_ROUTE_FRAGMENTS):
        raise PilotError("output root does not look like a private workspace path.")
    return output_root


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_review_csv(path: Path, review_table_dict: Mapping[str, object]) -> None:
    rows = review_table_dict.get("rows")
    columns = review_table_dict.get("columns")
    if not isinstance(rows, list) or not isinstance(columns, list):
        raise PilotError("room review table cannot be exported.")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[str(column) for column in columns])
        writer.writeheader()
        for row in rows:
            if isinstance(row, Mapping):
                writer.writerow(row)


def _write_public_summary(path: Path, summary: Mapping[str, object]) -> None:
    redacted = _redact_summary(summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redacted, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _redact_summary(summary: Mapping[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(summary))


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PilotError(f"{label} is not valid JSON.") from exc
    if not isinstance(data, Mapping):
        raise PilotError(f"{label} must be a JSON object.")
    return data


def _issue_codes(issues: Sequence[ValidationIssue]) -> str:
    return ", ".join(issue.code for issue in issues)


def _issue_to_public_dict(issue: ValidationIssue) -> dict[str, str]:
    return {"severity": issue.severity, "path": issue.path, "code": issue.code}


def _contains_forbidden_reference(value: object) -> bool:
    if isinstance(value, str):
        return _looks_like_path_or_url(value)
    if isinstance(value, Mapping):
        return any(_contains_forbidden_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_reference(item) for item in value)
    return False


def _looks_like_path_or_url(value: str) -> bool:
    lowered = value.lower()
    return any(fragment in lowered for fragment in FORBIDDEN_REF_FRAGMENTS)


def _looks_like_url(value: str) -> bool:
    lowered = value.lower()
    return "://" in lowered or lowered.startswith("www.")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


if __name__ == "__main__":
    raise SystemExit(run())
