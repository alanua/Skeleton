from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from scripts import aufmass_private_pilot_run


def test_dry_run_summary_does_not_expose_private_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    private_workspace = tmp_path / "operator_private_workspace"
    private_workspace.mkdir()
    manifest_path = _write_source_pack(private_workspace)

    code = aufmass_private_pilot_run.run(["--source-pack-manifest", str(manifest_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert str(private_workspace) not in captured.out
    assert "operator_private_workspace" not in captured.out
    assert "manual-only" in captured.out


def test_missing_source_pack_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = aufmass_private_pilot_run.run(
        ["--source-pack-manifest", str(tmp_path / "source_pack_manifest.json")]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "source pack manifest is missing" in captured.err
    assert str(tmp_path) not in captured.err


def test_dxf_branch_fails_closed_when_ezdxf_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_workspace = tmp_path / "private_workspace"
    output_root = private_workspace / "pilot_output"
    private_workspace.mkdir()
    manifest_path = _write_source_pack(private_workspace, source_type="dxf")

    def missing_ezdxf(name: str) -> object:
        if name == "ezdxf":
            raise ImportError("missing ezdxf")
        return importlib.import_module(name)

    monkeypatch.setattr(aufmass_private_pilot_run.importlib, "import_module", missing_ezdxf)

    code = aufmass_private_pilot_run.run(
        [
            "--source-pack-manifest",
            str(manifest_path),
            "--branch",
            "dxf-assisted",
            "--execute",
            "--private-workspace",
            str(private_workspace),
            "--output-root",
            str(output_root),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "requires ezdxf" in captured.err
    assert str(private_workspace) not in captured.err


def test_output_root_must_be_explicit_private_workspace_at_runtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_workspace = tmp_path / "private_workspace"
    private_workspace.mkdir()
    manifest_path = _write_source_pack(private_workspace)

    missing_output_code = aufmass_private_pilot_run.run(
        [
            "--source-pack-manifest",
            str(manifest_path),
            "--execute",
            "--private-workspace",
            str(private_workspace),
        ]
    )
    missing_output = capsys.readouterr()

    repo_output_code = aufmass_private_pilot_run.run(
        [
            "--source-pack-manifest",
            str(manifest_path),
            "--execute",
            "--private-workspace",
            str(private_workspace),
            "--output-root",
            str(Path.cwd() / "pilot_output"),
        ]
    )
    repo_output = capsys.readouterr()

    assert missing_output_code == 2
    assert "execution requires --output-root" in missing_output.err
    assert repo_output_code == 2
    assert "must not be written inside the public repository" in repo_output.err


def test_manual_only_branch_builds_expected_handoff_plan(tmp_path: Path) -> None:
    private_workspace = tmp_path / "private_workspace"
    private_workspace.mkdir()
    manifest_path = _write_source_pack(private_workspace, source_type="manual_room_list")
    args = aufmass_private_pilot_run.build_parser().parse_args(
        ["--source-pack-manifest", str(manifest_path), "--branch", "manual-only"]
    )

    plan = aufmass_private_pilot_run.plan_private_pilot_run(args)

    assert plan["mode"] == "dry-run"
    assert plan["branch"] == "manual-only"
    assert plan["private_artifacts"] == ["manual_input_handoff_check"]
    assert plan["manual_handoff"] == {
        "requires_operator_manual_input": True,
        "manual_input_provided": False,
        "engine_export_helper_ready": False,
        "execution_requires_manual_input": True,
    }


def test_private_pilot_manifest_rejects_path_like_private_ref(tmp_path: Path) -> None:
    private_workspace = tmp_path / "private_workspace"
    private_workspace.mkdir()
    manifest_path = _write_source_pack(private_workspace)
    private_manifest_path = private_workspace / "private_pilot_manifest.json"
    private_manifest_path.write_text(
        json.dumps(
            {
                "schema": "skeleton.aufmass_private_pilot_manifest.v1",
                "project_id": "aufmass",
                "route_stage": "input_sources",
                "private_refs": [
                    {
                        "private_ref": "private-ref-folder/file",
                        "source_type": "dxf",
                        "review_stage": "input_sources",
                        "artifact_kind": "input_source",
                        "public_safety_status": "private_only",
                    }
                ],
                "public_safety": {"status": "private_only"},
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    args = aufmass_private_pilot_run.build_parser().parse_args(
        [
            "--source-pack-manifest",
            str(manifest_path),
            "--private-pilot-manifest",
            str(private_manifest_path),
        ]
    )

    with pytest.raises(aufmass_private_pilot_run.PilotError, match="unsafe private_ref"):
        aufmass_private_pilot_run.plan_private_pilot_run(args)


def _write_source_pack(private_workspace: Path, *, source_type: str = "dxf") -> Path:
    scale_hint = {"basis": "not_applicable"}
    if source_type in {"dxf", "mixed"}:
        scale_hint = {"basis": "known_dimension", "detail": "operator supplied calibration"}

    manifest_path = private_workspace / "source_pack_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "skeleton.aufmass_source_pack.v1",
                "pack_id": "private-pilot-pack",
                "project_id": "aufmass",
                "sources": [
                    {
                        "source_id": "pilot-source-1",
                        "source_type": source_type,
                        "artifact_ref": "pilot-artifact-1",
                        "artifact_route": "private_local_runner",
                        "metadata": {
                            "title": "Private pilot source",
                            "source_revision": "rev-a",
                            "prepared_by": "operator",
                        },
                        "scale_hint": scale_hint,
                        "privacy_status": "private_pilot",
                        "review_status": "approved_for_private_intake",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path
