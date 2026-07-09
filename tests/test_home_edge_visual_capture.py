from __future__ import annotations

import struct
import sys
import types
import zlib
from pathlib import Path

import pytest

from core.home_edge.visual_capture import (
    BrowserFirstVisualCaptureAdapter,
    CaptureAdapterResult,
    VisualCaptureRuntimeConfig,
    validate_visual_capture_job,
)


def _png(width: int = 2, height: int = 1) -> bytes:
    pixels = b"\x00\x00\x00" * width * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = bytearray()
    stride = width * 3
    for y in range(height):
        rows.append(0)
        rows.extend(pixels[y * stride : (y + 1) * stride])
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(rows))),
            chunk(b"IEND", b""),
        )
    )


def _config(tmp_path: Path, *, visible: bool = False) -> VisualCaptureRuntimeConfig:
    profile = tmp_path / "profile"
    profile.mkdir(parents=True)
    return VisualCaptureRuntimeConfig(
        spool_root=tmp_path / "spool",
        artifact_root=tmp_path / "artifacts",
        browser_profile=profile,
        visible_kiosk_enabled=visible,
        yt_dlp_path=tmp_path / "bin" / "yt-dlp",
        ffmpeg_path=tmp_path / "bin" / "ffmpeg",
    )


def _job(*, capture_mode: str = "background", offsets_seconds: list[int] | None = None):
    return {
        "schema": "skeleton.home_edge.visual_capture.job.v1",
        "action_id": "capture-001",
        "task_ref": "task-001",
        "provider": "youtube",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "requested_time_seconds": 120,
        "offsets_seconds": offsets_seconds or [0],
        "capture_mode": capture_mode,
    }


class _Locator:
    first: "_Locator"

    def __init__(self, selector: str) -> None:
        self.selector = selector
        self.first = self

    def count(self) -> int:
        return 1 if self.selector in {"video.html5-main-video", "#movie_player"} else 0

    def is_visible(self, timeout: int = 0) -> bool:
        return self.count() == 1

    def screenshot(self, *, type: str, timeout: int) -> bytes:
        assert self.selector == "#movie_player"
        return _png()

    def bounding_box(self) -> dict[str, int]:
        return {"width": 2, "height": 1}


class _Page:
    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def locator(self, selector: str) -> _Locator:
        return _Locator(selector)

    def evaluate(self, script: str, arg: float | None = None):
        assert "Accept" not in script
        if arg is not None:
            assert arg == 120.0
            return None
        return {"decoded": 5, "currentTime": 120.0}

    def wait_for_timeout(self, milliseconds: int) -> None:
        return None


def test_browser_uses_existing_persistent_profile_and_background_headless(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import core.home_edge.visual_capture as visual

    config = _config(tmp_path)
    calls: list[dict] = []

    class FakeContext:
        def new_page(self) -> _Page:
            return _Page()

        def close(self) -> None:
            return None

    class FakeChromium:
        def launch(self, **kwargs):
            raise AssertionError("fresh browser launch must not be used")

        def launch_persistent_context(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["user_data_dir"] == str(config.browser_profile)
            assert kwargs["headless"] is True
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium()

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

    result = BrowserFirstVisualCaptureAdapter().capture(
        validate_visual_capture_job(_job(), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=tmp_path,
    )

    assert result.status == "CAPTURED"
    assert calls
    assert config.browser_profile.exists()


def test_visible_kiosk_is_non_headless_only_when_authorized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import core.home_edge.visual_capture as visual

    unauthorized = _config(tmp_path / "off", visible=False)
    with pytest.raises(Exception, match="visible_kiosk requires explicit private runtime selection"):
        validate_visual_capture_job(_job(capture_mode="visible_kiosk"), config=unauthorized)

    config = _config(tmp_path / "on", visible=True)
    headless_values: list[bool] = []

    class FakeContext:
        def new_page(self) -> _Page:
            return _Page()

        def close(self) -> None:
            return None

    class FakeChromium:
        def launch_persistent_context(self, **kwargs):
            headless_values.append(kwargs["headless"])
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium()

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

    result = BrowserFirstVisualCaptureAdapter().capture(
        validate_visual_capture_job(_job(capture_mode="visible_kiosk"), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=tmp_path,
    )

    assert result.status == "CAPTURED"
    assert headless_values == [False]


def test_missing_browser_profile_does_not_create_profile(tmp_path: Path) -> None:
    config = VisualCaptureRuntimeConfig(
        spool_root=tmp_path / "spool",
        artifact_root=tmp_path / "artifacts",
        browser_profile=tmp_path / "missing-profile",
    )

    result = BrowserFirstVisualCaptureAdapter().capture(
        validate_visual_capture_job(_job(), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=tmp_path,
    )

    assert result.status == "FAILED_RETRYABLE"
    assert result.reason_codes == ("private_runtime_missing",)
    assert not config.browser_profile.exists()


def test_fallback_seek_is_relative_to_downloaded_clip_and_temp_is_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import core.home_edge.visual_capture as visual

    config = _config(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["shell"] is False
        if argv[0] == str(config.yt_dlp_path):
            Path(str(argv[argv.index("-o") + 1]).replace("%(ext)s", "mp4")).write_bytes(b"clip")
        if argv[0] == str(config.ffmpeg_path):
            Path(argv[-1]).write_bytes(_png())
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(visual.subprocess, "run", fake_run)
    adapter = BrowserFirstVisualCaptureAdapter()
    monkeypatch.setattr(
        adapter,
        "_capture_with_browser",
        lambda job, *, normalized_url, config: CaptureAdapterResult(
            status="FAILED_RETRYABLE",
            reason_codes=("playwright_unavailable",),
            retryable=True,
        ),
    )

    result = adapter.capture(
        validate_visual_capture_job(_job(offsets_seconds=[-3, 0, 3]), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=tmp_path,
    )

    ffmpeg_seeks = [call[call.index("-ss") + 1] for call in calls if call[0] == str(config.ffmpeg_path)]
    for temporary in result.temporary_paths:
        visual._delete_temporary_paths((temporary,), tmp_path)

    assert result.status == "HUMAN_REVIEW_PENDING"
    assert ffmpeg_seeks == ["0.000", "3.000", "6.000"]
    assert not list(tmp_path.glob("media-fallback-*"))


@pytest.mark.parametrize("failed_tool", ["yt-dlp", "ffmpeg"])
def test_fallback_return_codes_fail_safely_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_tool: str,
) -> None:
    import core.home_edge.visual_capture as visual

    config = _config(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[0] == str(config.yt_dlp_path):
            if failed_tool == "yt-dlp":
                return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad")
            Path(str(argv[argv.index("-o") + 1]).replace("%(ext)s", "mp4")).write_bytes(b"clip")
        if argv[0] == str(config.ffmpeg_path):
            if failed_tool == "ffmpeg":
                Path(argv[-1]).write_bytes(_png())
                return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad")
            Path(argv[-1]).write_bytes(_png())
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(visual.subprocess, "run", fake_run)
    adapter = BrowserFirstVisualCaptureAdapter()
    monkeypatch.setattr(
        adapter,
        "_capture_with_browser",
        lambda job, *, normalized_url, config: CaptureAdapterResult(
            status="FAILED_RETRYABLE",
            reason_codes=("playwright_unavailable",),
            retryable=True,
        ),
    )

    result = adapter.capture(
        validate_visual_capture_job(_job(), config=config),
        normalized_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        config=config,
        output_dir=tmp_path,
    )

    for temporary in result.temporary_paths:
        visual._delete_temporary_paths((temporary,), tmp_path)

    assert result.status == "FAILED_RETRYABLE"
    assert result.reason_codes
    assert not list(tmp_path.glob("media-fallback-*"))
