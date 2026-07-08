from __future__ import annotations

import json
import os
import stat
import sys
import types
import zlib
from pathlib import Path

import pytest

from core.home_edge.visual_capture import (
    BrowserFirstVisualCaptureAdapter,
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
    profile.mkdir(parents=True, exist_ok=True)
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


def _png(width: int = 1, height: int = 1) -> bytes:
    import struct

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + (b"\xff\x00\x00" * width) for _ in range(height))
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(rows)),
            chunk(b"IEND", b""),
        )
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


def test_default_adapter_follows_browser_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    adapter = BrowserFirstVisualCaptureAdapter()
    called = False

    def fake_browser(job, *, normalized_url):
        nonlocal called
        called = True
        return CaptureAdapterResult(
            status="NOT_VISIBLE",
            reason_codes=("player_not_visible",),
        )

    monkeypatch.setattr(adapter, "_capture_with_browser", fake_browser)
    result = adapter.capture(
        validate_visual_capture_job(_job(), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=config.artifact_root,
    )

    assert called is True
    assert result.reason_codes == ("player_not_visible",)


def test_browser_path_uses_fixed_selectors_and_player_region_screenshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import core.home_edge.visual_capture as visual

    selected: list[str] = []
    accepted: list[str] = []

    class FakeLocator:
        def __init__(self, selector: str) -> None:
            self.selector = selector
            self.first = self

        def count(self) -> int:
            return 1 if self.selector in {"video.html5-main-video", "#movie_player"} else 0

        def is_visible(self, timeout: int = 0) -> bool:
            return self.count() == 1

        def screenshot(self, *, type: str, timeout: int) -> bytes:
            assert self.selector == "#movie_player"
            assert type == "png"
            return _png(2, 1)

        def bounding_box(self) -> dict[str, int]:
            return {"width": 2, "height": 1}

        def click(self) -> None:
            accepted.append(self.selector)

    class FakePage:
        def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        def locator(self, selector: str) -> FakeLocator:
            selected.append(selector)
            return FakeLocator(selector)

        def evaluate(self, script: str, arg: float | None = None):
            assert "Accept" not in script
            if arg is not None:
                assert arg == 120.0
                return None
            return {"decoded": 5, "currentTime": 120.0}

        def wait_for_timeout(self, milliseconds: int) -> None:
            assert milliseconds == visual.FRAME_STABLE_INTERVAL_MS

    class FakeBrowser:
        def new_page(self, **kwargs):
            return FakePage()

        def close(self) -> None:
            pass

    class FakePlaywright:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                assert kwargs["headless"] is True
                return FakeBrowser()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        types.SimpleNamespace(TimeoutError=TimeoutError, sync_playwright=lambda: FakePlaywright()),
    )
    monkeypatch.setattr(visual, "_fixed_chrome_executable", lambda: tmp_path / "chrome")

    config = _config(tmp_path)
    result = BrowserFirstVisualCaptureAdapter().capture(
        validate_visual_capture_job(_job(offsets_seconds=[0]), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=tmp_path,
    )

    assert result.status == "CAPTURED"
    assert result.frames[0].image_bytes.startswith(b"\x89PNG")
    assert "button:has-text('Accept all')" in selected
    assert "video.html5-main-video" in selected
    assert "#movie_player" in selected
    assert accepted == []


def test_browser_path_reports_interaction_without_accepting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import core.home_edge.visual_capture as visual

    class FakeLocator:
        def __init__(self, selector: str) -> None:
            self.selector = selector
            self.first = self

        def count(self) -> int:
            return 1 if "Accept all" in self.selector else 0

        def is_visible(self, timeout: int = 0) -> bool:
            return self.count() == 1

        def click(self) -> None:
            raise AssertionError("prompt must not be accepted")

    class FakePage:
        def goto(self, *args, **kwargs):
            pass

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator(selector)

    class FakeBrowser:
        def new_page(self, **kwargs):
            return FakePage()

        def close(self) -> None:
            pass

    class FakePlaywright:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                return FakeBrowser()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        types.SimpleNamespace(TimeoutError=TimeoutError, sync_playwright=lambda: FakePlaywright()),
    )
    monkeypatch.setattr(visual, "_fixed_chrome_executable", lambda: tmp_path / "chrome")

    config = _config(tmp_path)
    result = BrowserFirstVisualCaptureAdapter().capture(
        validate_visual_capture_job(_job(offsets_seconds=[0]), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=tmp_path,
    )

    assert result.status == "INTERACTION_REQUIRED"
    assert result.reason_codes == ("cookie_prompt_required",)


def test_fallback_uses_fixed_private_argv_and_deletes_temp_clip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []
    config = VisualCaptureRuntimeConfig(
        spool_root=tmp_path / "spool",
        artifact_root=tmp_path / "artifacts",
        browser_profile=tmp_path / "profile",
        yt_dlp_path=tmp_path / "bin" / "yt-dlp",
        ffmpeg_path=tmp_path / "bin" / "ffmpeg",
    )
    config.browser_profile.mkdir(parents=True)

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["shell"] is False
        if argv[0] == str(config.yt_dlp_path):
            output_index = argv.index("-o") + 1
            Path(str(argv[output_index]).replace("%(ext)s", "mp4")).write_bytes(b"clip")
        if argv[0] == str(config.ffmpeg_path):
            Path(argv[-1]).write_bytes(_png())
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("core.home_edge.visual_capture.subprocess.run", fake_run)
    adapter = BrowserFirstVisualCaptureAdapter()
    monkeypatch.setattr(
        adapter,
        "_capture_with_browser",
        lambda job, *, normalized_url: CaptureAdapterResult(
            status="FAILED_RETRYABLE",
            reason_codes=("playwright_unavailable",),
            retryable=True,
        ),
    )

    receipt = run_visual_capture_job(
        _job(offsets_seconds=[0]),
        config=config,
        adapter=adapter,
    )

    assert receipt["status"] == "HUMAN_REVIEW_PENDING"
    assert calls[0][0][0] == str(config.yt_dlp_path)
    assert calls[1][0][0] == str(config.ffmpeg_path)
    assert not list(config.artifact_root.glob("*/media-fallback-*"))


def test_contact_sheet_is_valid_png_and_manifest_hash_matches_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    receipt = run_visual_capture_job(
        _job(),
        config=config,
        adapter=FakeCaptureAdapter(CaptureAdapterResult(status="CAPTURED", frames=(_frame(),))),
    )
    capture_dir = next(config.artifact_root.iterdir())
    contact_sheet = capture_dir / "contact-sheet.private.bin"
    manifest = capture_dir / "manifest.private.json"

    assert contact_sheet.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert receipt["manifest_hash"] == __import__("hashlib").sha256(manifest.read_bytes()).hexdigest()


def test_human_review_pending_and_verified_are_valid_terminal_states(tmp_path: Path) -> None:
    for status in ("HUMAN_REVIEW_PENDING", "VERIFIED"):
        config = _config(tmp_path / status)
        receipt = run_visual_capture_job(
            _job(action_id=f"capture-{status.lower().replace('_', '-')}"),
            config=config,
            adapter=FakeCaptureAdapter(CaptureAdapterResult(status=status, frames=(_frame(),))),
        )

        assert receipt["status"] == status
