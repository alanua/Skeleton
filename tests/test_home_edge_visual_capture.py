from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from core.home_edge.visual_capture import (
    CaptureAdapterResult,
    CapturedFrame,
    JOB_SCHEMA,
    RECEIPT_SCHEMA,
    VisualCaptureError,
    VisualCaptureRuntimeConfig,
    process_one_visual_capture_job,
    run_visual_capture_job,
    runtime_config_from_env,
    validate_visual_capture_job,
)


class FakeCaptureAdapter:
    def __init__(self, result: CaptureAdapterResult) -> None:
        self.result = result
        self.calls = 0

    def capture(self, job, *, normalized_url, config, output_dir):
        self.calls += 1
        assert normalized_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        return self.result


def _config(tmp_path: Path, *, visible_kiosk: bool = False) -> VisualCaptureRuntimeConfig:
    spool = tmp_path / "spool"
    artifact = tmp_path / "artifacts"
    profile = tmp_path / "profile"
    profile.mkdir(exist_ok=True)
    return VisualCaptureRuntimeConfig(
        spool_root=spool,
        artifact_root=artifact,
        browser_profile=profile,
        visible_kiosk_enabled=visible_kiosk,
    )


def _job(**updates: object) -> dict[str, object]:
    job: dict[str, object] = {
        "schema": JOB_SCHEMA,
        "action_id": "capture-001",
        "task_ref": "task-001",
        "provider": "youtube",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=90s",
        "requested_time_seconds": 120,
    }
    job.update(updates)
    return job


def _frame(*, observed: float = 120.0) -> CapturedFrame:
    return CapturedFrame(
        offset_seconds=0,
        requested_time_seconds=120.0,
        observed_time_seconds=observed,
        width=640,
        height=360,
        image_bytes=b"synthetic-private-frame",
    )


def test_provider_and_url_allowlist_failures(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with pytest.raises(VisualCaptureError, match="provider"):
        validate_visual_capture_job(_job(provider="vimeo"), config=config)
    with pytest.raises(VisualCaptureError, match="YouTube"):
        validate_visual_capture_job(_job(url="https://youtu.be/dQw4w9WgXcQ"), config=config)
    with pytest.raises(VisualCaptureError, match="YouTube"):
        validate_visual_capture_job(_job(url="https://www.youtube.com/embed/dQw4w9WgXcQ"), config=config)


def test_malformed_timestamp_offset_and_more_than_seven_frame_failures(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with pytest.raises(VisualCaptureError, match="requested_time_seconds"):
        validate_visual_capture_job(_job(requested_time_seconds="120"), config=config)
    with pytest.raises(VisualCaptureError, match="integers"):
        validate_visual_capture_job(_job(offsets_seconds=[0, 1.5]), config=config)
    with pytest.raises(VisualCaptureError, match="-10..10"):
        validate_visual_capture_job(_job(offsets_seconds=[-11]), config=config)
    with pytest.raises(VisualCaptureError, match="seven"):
        validate_visual_capture_job(_job(offsets_seconds=[-3, -2, -1, 0, 1, 2, 3, 4]), config=config)


def test_issue_controlled_path_selector_and_command_fields_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)

    for field in ("command", "selector", "executable_path", "output_path", "host", "user", "port"):
        with pytest.raises(VisualCaptureError, match="issue-controlled"):
            validate_visual_capture_job(_job(**{field: "bad"}), config=config)


def test_repository_artifact_root_symlink_and_traversal_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    with pytest.raises(VisualCaptureError, match="outside"):
        runtime_config_from_env(
            {
                "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_SPOOL": str(tmp_path / "spool"),
                "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_ARTIFACT_ROOT": str(Path.cwd() / "private"),
                "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_BROWSER_PROFILE": str(profile),
            }
        )
    if hasattr(os, "symlink"):
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(VisualCaptureError, match="symlink"):
            runtime_config_from_env(
                {
                    "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_SPOOL": str(tmp_path / "spool"),
                    "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_ARTIFACT_ROOT": str(link / "artifacts"),
                    "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_BROWSER_PROFILE": str(profile),
                }
            )


def test_duplicate_idempotency_returns_existing_receipt(tmp_path: Path) -> None:
    config = _config(tmp_path)
    adapter = FakeCaptureAdapter(CaptureAdapterResult(status="CAPTURED", frames=(_frame(),)))

    first = run_visual_capture_job(_job(), config=config, adapter=adapter)
    second = run_visual_capture_job(_job(), config=config, adapter=adapter)

    assert first == second
    assert adapter.calls == 1


def test_visible_kiosk_requires_explicit_private_job_selection(tmp_path: Path) -> None:
    with pytest.raises(VisualCaptureError, match="visible_kiosk"):
        validate_visual_capture_job(_job(capture_mode="visible_kiosk"), config=_config(tmp_path))

    normalized = validate_visual_capture_job(
        _job(capture_mode="visible_kiosk"),
        config=_config(tmp_path, visible_kiosk=True),
    )
    assert normalized["capture_mode"] == "visible_kiosk"


def test_timestamp_drift_returns_stable_reason(tmp_path: Path) -> None:
    config = _config(tmp_path)
    receipt = run_visual_capture_job(
        _job(),
        config=config,
        adapter=FakeCaptureAdapter(CaptureAdapterResult(status="CAPTURED", frames=(_frame(observed=123.0),))),
    )

    assert receipt["status"] == "NEEDS_RECAPTURE"
    manifest_path = next(config.artifact_root.glob("*/manifest.private.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["frames"][0]["reason_codes"] == ["timestamp_drift_exceeded"]


def test_interaction_required_states_do_not_auto_accept_prompts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    receipt = run_visual_capture_job(
        _job(),
        config=config,
        adapter=FakeCaptureAdapter(
            CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("cookie_prompt_required",),
                retryable=True,
            )
        ),
    )

    assert receipt["status"] == "INTERACTION_REQUIRED"
    assert receipt["reason_codes"] == ["cookie_prompt_required"]


def test_sanitized_receipt_leakage_checks(tmp_path: Path) -> None:
    config = _config(tmp_path)
    receipt = run_visual_capture_job(
        _job(),
        config=config,
        adapter=FakeCaptureAdapter(CaptureAdapterResult(status="CAPTURED", frames=(_frame(),))),
    )
    assert set(receipt) == {
        "schema",
        "action_id",
        "task_ref",
        "status",
        "frame_count",
        "manifest_hash",
        "capture_mode",
        "reason_codes",
        "retryable",
        "human_review_required",
        "stale",
    }
    rendered = json.dumps(receipt, sort_keys=True)
    assert "youtube" not in rendered
    assert "dQw4w9WgXcQ" not in rendered
    assert str(tmp_path) not in rendered
    assert RECEIPT_SCHEMA == receipt["schema"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode checks only")
def test_owner_only_file_checks_on_posix(tmp_path: Path) -> None:
    config = _config(tmp_path)
    run_visual_capture_job(
        _job(),
        config=config,
        adapter=FakeCaptureAdapter(CaptureAdapterResult(status="CAPTURED", frames=(_frame(),))),
    )
    for path in config.artifact_root.glob("*/*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600


def test_temporary_clip_deletion_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class TempClipAdapter:
        def capture(self, job, *, normalized_url, config, output_dir):
            clip = output_dir / "clip.tmp"
            clip.write_bytes(b"temporary-private-clip")
            return CaptureAdapterResult(
                status="CAPTURED",
                frames=(_frame(),),
                temporary_paths=(clip,),
            )

    run_visual_capture_job(_job(), config=config, adapter=TempClipAdapter())

    assert not list(config.artifact_root.glob("*/clip.tmp"))


def test_no_derived_memory_authority_promotion(tmp_path: Path) -> None:
    config = _config(tmp_path)
    receipt = run_visual_capture_job(
        _job(),
        config=config,
        adapter=FakeCaptureAdapter(CaptureAdapterResult(status="CAPTURED", frames=(_frame(),))),
    )
    manifest = json.loads(next(config.artifact_root.glob("*/manifest.private.json")).read_text(encoding="utf-8"))

    assert manifest["evidence_state"] == "private_manifest_only"
    assert "Graphify" not in json.dumps(receipt)
    assert "MemPalace" not in json.dumps(receipt)


def test_bounded_worker_processes_exactly_one_private_spool_job(tmp_path: Path) -> None:
    config = _config(tmp_path)
    queued = config.spool_root / "queued"
    queued.mkdir(parents=True)
    (queued / "001.json").write_text(json.dumps(_job(action_id="capture-001")), encoding="utf-8")
    (queued / "002.json").write_text(json.dumps(_job(action_id="capture-002")), encoding="utf-8")
    adapter = FakeCaptureAdapter(CaptureAdapterResult(status="CAPTURED", frames=(_frame(),)))

    receipt = process_one_visual_capture_job(config=config, adapter=adapter)

    assert receipt["status"] == "CAPTURED"
    assert adapter.calls == 1
    assert (config.spool_root / "done" / "001.json").exists()
    assert (queued / "002.json").exists()
